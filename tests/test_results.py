"""Ordinary-result contracts, using synthetic bytes and no ML/container runtime."""

import csv
import hashlib
import json
from pathlib import Path
import re
import shutil

import pytest

from mewc_pipeline import results as r


def put_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def build_case(tmp_path, descriptions=None):
    stages = {name: tmp_path / name for name in ("detect", "snip", "exif", "box")}
    for path in stages.values():
        path.mkdir()
    descriptions = descriptions or [("originals/north/a.jpg", "animal"),
                    ("originals/south/a.jpg", "animal"),
                    ("originals/empty-camera/empty.jpg", "blank"),
                    ("originals/people/walker.jpg", "person"),
                    ("originals/traffic/car.jpg", "vehicle")]
    images, box_rows, crops, table, sources = [], [], [], [], {}
    for name, category in descriptions:
        code = {"animal": "1", "person": "2", "vehicle": "3"}.get(category)
        detections = [] if code is None else [dict(category=code, conf=.9, bbox=[.1, .1, .2, .2])]
        images.append(dict(file=name, detections=detections))
        sources[name] = {"sha256": hashlib.sha256(name.encode()).hexdigest(), "size": len(name)}
        output = "boxed/" + category + "/" + name
        boxed = stages["box"] / output
        boxed.parent.mkdir(parents=True, exist_ok=True)
        boxed.write_bytes(("finished:" + name).encode())
        box_rows.append(dict(source_file=name, output_file=output,
                             output_sha256=hashlib.sha256(boxed.read_bytes()).hexdigest(),
                             status="copied" if category == "blank" else "rendered"))
        if category == "animal":
            crop = name.removesuffix(".jpg") + "-0.jpg"
            path = stages["snip"] / "snips" / crop
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("crop:" + name).encode())
            crops.append(dict(crop_id=crop, crop_file=crop, source_file=name, detection_index=0))
            for rank in (1, 2):
                table.append(dict(crop_id=crop, filename=crop, rand_name=crop, crop_file=crop,
                                  source_file=name, detection_index=0, class_rank=rank,
                                  class_name="species", prob=.8 if rank == 1 else .2,
                                  metadata_status="exported", date_time_orig="2026:01:02 03:04:05"))
    put_json(stages["detect"] / "md_out.json", dict(images=images,
             detection_categories={"1": "animal", "2": "person", "3": "vehicle"}))
    put_json(stages["snip"] / "snips/crop_manifest.json", dict(complete=True, errors=[], crops=crops))
    put_json(stages["box"] / "boxed/box_report.json", dict(complete=True, errors=[], draw=True,
             subfolder=True, sort_policy="highest_confidence", images=box_rows))
    csv_path = stages["exif"] / "metadata/mewc_out.csv"
    csv_path.parent.mkdir()
    with csv_path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["crop_id", "filename", "rand_name", "crop_file",
            "source_file", "detection_index", "class_rank", "class_name", "prob", "metadata_status", "date_time_orig"])
        writer.writeheader()
        writer.writerows(table)
    return dict(target=tmp_path / "delivery.partial", stages=stages, sources=sources,
                fingerprint="verified-fixture", receipt_path=tmp_path / "private/receipt.json")


@pytest.fixture
def case(tmp_path):
    return build_case(tmp_path)


def materialize(case, **kwargs):
    return r.materialize(**case, **kwargs)


def test_deliverable_contains_only_finished_images_crops_and_final_tables(case):
    receipt = materialize(case)
    target = case["target"]
    leaves = {"north", "south", "empty-camera", "people", "traffic"}
    assert {p.name for p in target.iterdir()} == leaves
    assert set(receipt["leaves"]) == leaves
    for leaf in leaves:
        assert {p.name for p in (target / leaf).iterdir()} == set(r.DIRECTORIES) | {"md_out.json", "mewc_out.csv"}
    assert receipt["complete"] is True
    assert receipt["destination"] == str(target)
    assert receipt["rename_snips"] is True
    assert receipt["snip_chars"] == 16
    assert r.verify(target, receipt)["files"] == 17
    assert (target / "empty-camera/blank/empty.jpg").read_bytes() == b"finished:originals/empty-camera/empty.jpg"
    assert (target / "north/animal/a.jpg").read_bytes() == b"finished:originals/north/a.jpg"
    rows = []
    for leaf in ("north", "south"):
        md = json.loads((target / leaf / "md_out.json").read_text())
        assert [im["file"] for im in md["images"]] == ["animal/a.jpg"]
        assert md["images"][0]["detections"][0]["bbox"] == [.1, .1, .2, .2]
        with (target / leaf / "mewc_out.csv").open(newline="") as stream:
            rows.extend(csv.DictReader(stream))
        assert all(re.fullmatch(r"[A-Za-z0-9]{16}\.jpg", p.name) for p in (target / leaf / "snips").iterdir())
    assert {row["source_file"] for row in rows} == {"animal/a.jpg"}
    assert {row["crop_id"] for row in rows} == {"a-0.jpg"}
    assert all(row["filename"] == row["crop_id"] and row["detection_index"] == "0" for row in rows)
    assert all(row["metadata_status"] == "exported" and row["date_time_orig"] == "2026:01:02 03:04:05" for row in rows)
    assert {row["rand_name"] for row in rows} == set(receipt["random_names"].values())
    assert all(row["crop_file"] == "snips/" + row["rand_name"] for row in rows)
    # The classifier's original table and crop identities remain immutable.
    with (case["stages"]["exif"] / "metadata/mewc_out.csv").open() as stream:
        assert all(row["source_file"].startswith("originals/") for row in csv.DictReader(stream))


def test_blank_only_keeps_empty_category_directories_and_csv_header(tmp_path):
    case = build_case(tmp_path, [("originals/empty.jpg", "blank")])
    receipt = materialize(case)
    assert r.verify(case["target"], receipt)["files"] == 3
    assert receipt["random_names"] == {}
    assert receipt["leaves"] == [""]
    assert all((case["target"] / directory).is_dir() for directory in r.DIRECTORIES)
    assert not list((case["target"] / "snips").iterdir())
    with (case["target"] / "mewc_out.csv").open() as stream:
        assert list(csv.DictReader(stream)) == []


def test_flat_leaf_has_two_root_tables_and_five_directories(tmp_path):
    case = build_case(tmp_path, [("originals/a.jpg", "animal"), ("originals/empty.jpg", "blank")])
    receipt = materialize(case)
    assert {p.name for p in case["target"].iterdir()} == set(r.DIRECTORIES) | {"md_out.json", "mewc_out.csv"}
    assert receipt["leaves"] == [""]
    assert r.verify(case["target"], receipt)["files"] == 5


def test_nested_branches_have_only_leaf_deliverables_and_blank_leaf(tmp_path):
    case = build_case(tmp_path, [("originals/site/north/a.jpg", "animal"),
                                 ("originals/site/south/a.jpg", "animal"),
                                 ("originals/other/empty.jpg", "blank")])
    receipt = materialize(case)
    target = case["target"]
    assert {p.name for p in target.iterdir()} == {"site", "other"}
    assert {p.name for p in (target / "site").iterdir()} == {"north", "south"}
    assert set(receipt["leaves"]) == {"site/north", "site/south", "other"}
    for leaf in receipt["leaves"]:
        assert {p.name for p in (target / leaf).iterdir()} == set(r.DIRECTORIES) | {"md_out.json", "mewc_out.csv"}
        md = json.loads((target / leaf / "md_out.json").read_text())
        assert len(md["images"]) == 1
        assert (target / leaf / md["images"][0]["file"]).is_file()
    with (target / "other/mewc_out.csv").open() as stream:
        assert list(csv.DictReader(stream)) == []
    r.verify(target, receipt)


@pytest.mark.parametrize("names", [
    [("originals/a.jpg", "animal"), ("originals/nested/b.jpg", "blank")],
    [("originals/site/a.jpg", "animal"), ("originals/site/camera/b.jpg", "blank")],
])
def test_branch_and_leaf_images_are_rejected(tmp_path, names):
    case = build_case(tmp_path, names)
    with pytest.raises(r.Invalid, match="branch and a leaf"):
        materialize(case)
    assert not case["target"].exists()


def test_random_filename_collision_retries_and_reuses_mapping(case, monkeypatch):
    tokens = iter("A" * 32 + "B" * 16)
    monkeypatch.setattr(r.secrets, "choice", lambda alphabet: next(tokens))
    receipt = materialize(case)
    assert set(receipt["random_names"].values()) == {"A" * 16 + ".jpg", "B" * 16 + ".jpg"}
    monkeypatch.setattr(r.secrets, "choice", lambda alphabet: pytest.fail("Retry attempted new random naming"))
    assert materialize(case) == receipt


def test_random_names_avoid_case_insensitive_collisions(case, monkeypatch):
    tokens = iter("A" * 16 + "a" * 16 + "B" * 16)
    monkeypatch.setattr(r.secrets, "choice", lambda alphabet: next(tokens))
    receipt = materialize(case)
    assert set(receipt["random_names"].values()) == {"A" * 16 + ".jpg", "B" * 16 + ".jpg"}


@pytest.mark.parametrize("names", [
    [("originals/camera/a.jpg", "animal"), ("originals/camera/A.jpg", "blank")],
    [("originals/North/a.jpg", "animal"), ("originals/north/b.jpg", "blank")],
])
def test_case_insensitive_image_or_leaf_collision_is_rejected(tmp_path, names):
    case = build_case(tmp_path, names)
    with pytest.raises(r.Invalid, match="Case-insensitive"):
        materialize(case)
    assert not case["target"].exists()


def test_export_omits_internal_detector_path_rebase_metadata(case):
    path = case["stages"]["detect"] / "md_out.json"
    detector = json.loads(path.read_text())
    detector["mewc_path_rebase"] = {"source_artifact": "md_raw.json", "prefix": "originals/"}
    detector["info"] = {"detector": "fixture"}
    put_json(path, detector)
    receipt = materialize(case)
    for leaf in receipt["leaves"]:
        actual = json.loads((case["target"] / leaf / "md_out.json").read_text())
        assert "mewc_path_rebase" not in actual
        assert actual["info"] == {"detector": "fixture"}
    assert "mewc_path_rebase" in json.loads(path.read_text())


def test_random_filename_collision_exhaustion_is_bounded(case, monkeypatch):
    monkeypatch.setattr(r.secrets, "choice", lambda alphabet: "A")
    with pytest.raises(r.Invalid, match="unique random"):
        materialize(case)
    assert not case["target"].exists()
    assert not case["receipt_path"].exists()


def test_nonrandom_mode_preserves_nested_snip_names(case):
    receipt = materialize(case, rename_snips=False)
    assert set(receipt["random_names"].values()) == {"a-0.jpg"}
    assert (case["target"] / "north/snips/a-0.jpg").is_file()
    r.verify(case["target"], receipt)


def test_interrupted_copy_owns_partial_and_preserves_names(case, monkeypatch):
    calls = []
    def guard(path):
        calls.append(path)
        if path == case["target"] / "north/animal/a.jpg":
            raise RuntimeError("withdrawn admission")
    with pytest.raises(RuntimeError, match="withdrawn"):
        materialize(case, guard=guard)
    previous = json.loads(case["receipt_path"].read_text())
    assert previous["complete"] is False
    assert case["target"].is_dir()
    assert case["receipt_path"] in calls
    monkeypatch.setattr(r.secrets, "choice", lambda alphabet: pytest.fail("Lost persisted mapping"))
    receipt = materialize(case)
    assert receipt["random_names"] == previous["random_names"]
    r.verify(case["target"], receipt)


@pytest.mark.parametrize("extra", ["extra.json", "north/animal/extra.jpg", "unused-empty-directory"])
def test_verification_rejects_extras(case, extra):
    receipt = materialize(case)
    path = case["target"] / extra
    if extra == "unused-empty-directory":
        path.mkdir()
    else:
        path.write_bytes(b"extra")
    with pytest.raises(r.Invalid, match="differs"):
        r.verify(case["target"], receipt)
    with pytest.raises(r.Invalid, match="Unexpected"):
        materialize(case)


def test_verification_rejects_tamper_and_accepts_verified_relocation(case, tmp_path):
    receipt = materialize(case)
    transferred = tmp_path / "returned-to-source"
    shutil.copytree(case["target"], transferred)
    assert r.verify(transferred, receipt)["complete"]
    (transferred / "empty-camera/blank/empty.jpg").write_bytes(b"changed")
    with pytest.raises(r.Invalid, match="differs"):
        r.verify(transferred, receipt)


def test_existing_partial_without_external_receipt_is_rejected(case):
    case["target"].mkdir()
    with pytest.raises(r.Invalid, match="ownership"):
        materialize(case)


def test_receipt_cannot_be_inside_deliverable(case):
    case["receipt_path"] = case["target"] / "receipt.json"
    with pytest.raises(r.Invalid, match="outside"):
        materialize(case)


def test_receipt_cannot_modify_immutable_stage_tree(case):
    case["receipt_path"] = case["stages"]["box"] / "receipt.json"
    with pytest.raises(r.Invalid, match="immutable"):
        materialize(case)


def test_rebasing_image_filename_collision_is_rejected(case):
    stages = case["stages"]
    old, new = "originals/south/a.jpg", "north/a.jpg"
    md = json.loads((stages["detect"] / "md_out.json").read_text())
    next(image for image in md["images"] if image["file"] == old)["file"] = new
    put_json(stages["detect"] / "md_out.json", md)
    report_path = stages["box"] / "boxed/box_report.json"
    report = json.loads(report_path.read_text())
    row = next(row for row in report["images"] if row["source_file"] == old)
    old_path = stages["box"] / row["output_file"]
    row.update(source_file=new, output_file="boxed/animal/" + new)
    new_path = stages["box"] / row["output_file"]
    new_path.parent.mkdir(parents=True)
    old_path.rename(new_path)
    put_json(report_path, report)
    case["sources"][new] = case["sources"].pop(old)
    with pytest.raises(r.Invalid, match="filename collision"):
        materialize(case)


def test_receipt_from_another_run_is_rejected(case):
    materialize(case)
    case["fingerprint"] = "another-run"
    with pytest.raises(r.Invalid, match="belong"):
        materialize(case)


@pytest.mark.parametrize("location", ["result", "source", "receipt", "parent"])
def test_symlinks_are_rejected(case, tmp_path, location):
    if location == "source":
        path = case["stages"]["box"] / "boxed/animal/originals/north/a.jpg"
        real = tmp_path / "outside.jpg"
        path.rename(real)
        path.symlink_to(real)
    elif location == "receipt":
        real = tmp_path / "external-receipt.json"
        real.write_text("{}")
        case["receipt_path"].parent.mkdir()
        case["receipt_path"].symlink_to(real)
    elif location == "parent":
        real = tmp_path / "actual"
        real.mkdir()
        link = tmp_path / "linked-parent"
        link.symlink_to(real, target_is_directory=True)
        case["target"] = link / "delivery.partial"
    else:
        materialize(case)
        (case["target"] / "north/snips/linked.jpg").symlink_to(tmp_path / "absent")
    with pytest.raises(r.Invalid, match="Symlink"):
        materialize(case)


@pytest.mark.parametrize("bad_category", ["mixed", "human", "unknown"])
def test_unrecognized_category_is_rejected(case, bad_category):
    report_path = case["stages"]["box"] / "boxed/box_report.json"
    report = json.loads(report_path.read_text())
    report["images"][0]["output_file"] = f"boxed/{bad_category}/originals/north/a.jpg"
    put_json(report_path, report)
    with pytest.raises(r.Invalid, match="unrecognized"):
        materialize(case)


@pytest.mark.parametrize("key,value", [("draw", False), ("sort_policy", "mixed"), ("complete", False)])
def test_wrong_box_semantics_are_not_presented_as_success(case, key, value):
    path = case["stages"]["box"] / "boxed/box_report.json"
    report = json.loads(path.read_text())
    report[key] = value
    put_json(path, report)
    with pytest.raises(r.Invalid, match="complete drawn"):
        materialize(case)


def test_prediction_csv_cannot_substitute_for_final_exif_csv(case):
    path = case["stages"]["exif"] / "metadata/mewc_out.csv"
    with path.open() as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        fields = [field for field in reader.fieldnames if field != "metadata_status"]
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(r.Invalid, match="EXIF-enriched"):
        materialize(case)


def test_changed_source_during_copy_does_not_complete_receipt(case):
    source = case["stages"]["box"] / "boxed/animal/originals/north/a.jpg"
    def guard(path):
        if path == case["target"]:
            source.write_bytes(b"changed after verification")
    with pytest.raises(r.Invalid, match="differs|changed"):
        materialize(case, guard=guard)
    assert json.loads(case["receipt_path"].read_text())["complete"] is False


@pytest.mark.parametrize("stage,extra", [("box", "boxed/unexpected.log"), ("snip", "snips/unexpected.jpg")])
def test_unaccounted_stage_files_are_rejected(case, stage, extra):
    (case["stages"][stage] / extra).write_bytes(b"unexpected")
    with pytest.raises(r.Invalid, match="Unexpected"):
        materialize(case)


def test_unsafe_output_path_is_rejected(case):
    path = case["stages"]["box"] / "boxed/box_report.json"
    report = json.loads(path.read_text())
    report["images"][0]["output_file"] = "boxed/animal/../../outside.jpg"
    put_json(path, report)
    with pytest.raises(r.Invalid, match="Unsafe"):
        materialize(case)
