"""Run inside the locked snip image to independently recompute crop eligibility."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/code")
from lib_tools import process_detections

root = Path("/images")
data = json.loads((root / "md_out.json").read_text())
manifest = json.loads((root / "snips/crop_manifest.json").read_text())
expected = set()
for image in data["images"]:
    mask = process_detections(
        image,
        float(os.environ["OVERLAP"]),
        float(os.environ["EDGE_DIST"]),
        int(os.environ["MIN_EDGES"]),
        float(os.environ["UPPER_CONF"]),
        float(os.environ["LOWER_CONF"]),
        policy=os.environ["SUPPRESSION_POLICY"],
    )
    expected.update(
        (image["file"], i)
        for i, (det, keep) in enumerate(zip(image["detections"], mask))
        if keep and det["category"] == "1"
    )
actual = {(row["source_file"], row["detection_index"]) for row in manifest["crops"]}
if expected != actual or len(actual) != len(manifest["crops"]):
    raise ValueError("Policy-eligible detector identities differ from crop manifest")
print(json.dumps({"complete": True, "eligible_crops": len(expected)}))
