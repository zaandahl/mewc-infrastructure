"""Validate all prediction representations inside the locked classifier image."""

import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/code")
import numpy as np
import pandas as pd
from lib_common import read_yaml
from prediction_contract import (
    class_names_in_order,
    crop_inventory,
    prediction_table,
    validate_predictions,
)

root = Path("/images")
marker = json.loads((root / "prediction_manifest.json").read_text())
inventory, mode = crop_inventory(root / "snips")
if mode != "crop-manifest-v1":
    raise ValueError("Pipeline requires canonical crop identities")
declaration = json.loads(Path("/models/manifest.json").read_text())
ids = declaration["class_ids"]
names = class_names_in_order(read_yaml("/models/class_map.yaml"), ids)
with np.load(root / "prediction_scores.npz", allow_pickle=False) as scores:
    if set(scores.files) != {"probabilities", "crop_ids", "class_order", "class_ids"}:
        raise ValueError("Unexpected score archive schema")
    values = scores["probabilities"]
    if scores["class_ids"].tolist() != ids:
        raise ValueError("Score matrix class code order mismatch")
    if scores["crop_ids"].tolist() != [row["crop_id"] for row in inventory]:
        raise ValueError("Score matrix crop order mismatch")
    if scores["class_order"].tolist() != names:
        raise ValueError("Score matrix class order mismatch")
validate_predictions(values, len(inventory), len(names))
top = os.environ["TOP_CLASSES"].lower() == "true"
expected = prediction_table(values, inventory, names, top, ids)
pd.testing.assert_frame_equal(pd.read_pickle(root / "mewc_out.pkl"), expected)
pd.testing.assert_frame_equal(
    pd.read_csv(root / "mewc_out.csv"),
    pd.read_csv(io.StringIO(expected.to_csv(index=False))),
)
for key, value in dict(
    crop_count=len(inventory),
    prediction_count=len(inventory),
    output_row_count=len(expected),
    class_count=len(names),
    top_classes=top,
    tie_policy="retain-all-exact-maxima",
    identity_mode="crop-manifest-v1",
    model_runtime_validated=bool(inventory),
).items():
    if marker.get(key) != value:
        raise ValueError("Prediction provenance differs: " + key)
print(json.dumps({"complete": True, "crops": len(inventory), "rows": len(expected)}))
