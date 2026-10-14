"""Materialize the ordinary MEWC deliverable from verified immutable stages.

The export receipt belongs to the controller's private run state, never to the
user's result tree. Crop identities remain immutable inside the pipeline; only
the exported copies receive the historical random filename/``rand_name`` map.
"""

import csv
import hashlib
import io
import json
import os
import secrets
import shutil
import stat
import string
import tempfile
from pathlib import Path, PurePosixPath

DIRECTORIES = ("animal", "blank", "person", "snips", "vehicle")
FORMAT = "mewc-standard-v1"


class Invalid(ValueError):
    pass


def _require(value, message):
    if not value:
        raise Invalid(message)


def _relative(value):
    _require(isinstance(value, str) and value and "\\" not in value,
             "Expected a POSIX relative result path")
    name = PurePosixPath(value)
    _require(not name.is_absolute() and all(p not in ("", ".", "..") for p in value.split("/")),
             f"Unsafe result path: {value!r}")
    return value


def _path(value):
    path = Path(value)
    _require(path.is_absolute() and ".." not in path.parts, "Expected an absolute result path")
    _require(not any(p.is_symlink() for p in (path, *path.parents)),
             f"Symlink is unsupported: {path}")
    return path


def _write_path(path, guard):
    path = _path(path)
    if guard is not None:
        guard(path)
    return _path(path)


def _mkdir(path, guard):
    path = _write_path(path, guard)
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    _require(path.is_dir(), f"Result parent is not a directory: {path}")
    for directory in reversed(missing):
        _write_path(directory, guard).mkdir()


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _sha(path):
    digest = hashlib.sha256()
    with _path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file(path):
    path = _path(path)
    _require(path.is_file() and stat.S_ISREG(path.stat().st_mode), f"Missing regular result source: {path}")
    return path


def _json(path):
    with _file(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def _atomic_bytes(path, payload, guard):
    path = _write_path(path, guard)
    _mkdir(path.parent, guard)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".mewc-result-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            _write_path(temporary, guard)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(_write_path(temporary, guard), _write_path(path, guard))
    finally:
        if temporary.exists():
            _write_path(temporary, guard).unlink()


def _copy(source, destination, guard):
    source = _file(source)
    destination = _write_path(destination, guard)
    _mkdir(destination.parent, guard)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".mewc-result-", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        _write_path(temporary, guard)
        shutil.copy2(_file(source), _write_path(temporary, guard))
        os.replace(_write_path(temporary, guard), _write_path(destination, guard))
    finally:
        if temporary.exists():
            _write_path(temporary, guard).unlink()


def _inventory(root):
    root = _path(root)
    _require(root.is_dir(), "Result directory is missing")
    files, directories = {}, set()
    for path in sorted(root.rglob("*")):
        _path(path)
        name = _relative(path.relative_to(root).as_posix())
        if path.is_dir():
            directories.add(name)
        else:
            _file(path)
            files[name] = {"sha256": _sha(path), "size": path.stat().st_size}
    return files, directories


def _join(leaf, name):
    return leaf + "/" + name if leaf else name


def _parents(names, leaves):
    directories = {_join(leaf, directory) for leaf in leaves for directory in DIRECTORIES}
    for name in names:
        directories.update(str(p) for p in PurePosixPath(name).parents if str(p) != ".")
    return sorted(directories)


def _no_case_collisions(names):
    names = set(names)
    _require(len({name.casefold() for name in names}) == len(names),
             "Case-insensitive result filename/directory collision")


def _leaf_map(names):
    rebased = [_unprefix(name) for name in names]
    _require(len(set(rebased)) == len(rebased), "Rebased image filename collision")
    _no_case_collisions(rebased)
    result = {name: str(PurePosixPath(_unprefix(name)).parent) for name in names}
    result = {name: "" if leaf == "." else leaf for name, leaf in result.items()}
    leaves = set(result.values())
    for leaf in leaves:
        _require(not any(other != leaf and (not leaf or other.startswith(leaf + "/")) for other in leaves),
                 "Images occur in both a branch and a leaf folder")
    return result


def _crop_local(crop, leaf):
    name = _unprefix(crop)
    if leaf:
        _require(name.startswith(leaf + "/"), "Crop does not belong to its source leaf")
        name = name[len(leaf) + 1:]
    _require("/" not in name, "Crop does not share its source image folder")
    return name


def _assert_artifacts(artifacts):
    for name, expected in artifacts.items():
        path = _file(name)
        _require(path.stat().st_size == expected["size"] and _sha(path) == expected["sha256"],
                 "Verified stage artifact changed during result materialization")


def _unprefix(name):
    return _relative(name).removeprefix("originals/")


def _load_sources(stages, sources):
    _require(isinstance(sources, dict) and sources, "Verified source images are required")
    names = {_relative(name) for name in sources}
    leaf_map = _leaf_map(names)
    paths = {stage: _path(stages[stage]) for stage in ("detect", "snip", "exif", "box")}
    detector_path = _file(paths["detect"] / "md_out.json")
    crop_manifest_path = _file(paths["snip"] / "snips/crop_manifest.json")
    box_report_path = _file(paths["box"] / "boxed/box_report.json")
    csv_path = _file(paths["exif"] / "metadata/mewc_out.csv")
    detector, crops, report = (_json(p) for p in (detector_path, crop_manifest_path, box_report_path))
    _require(isinstance(detector.get("images"), list), "Detector image list is missing")
    images = {item["file"]: item for item in detector["images"]}
    _require(len(images) == len(detector["images"]) and set(images) == names,
             "Detector/source identity set differs")
    _require(detector.get("detection_categories") == {"1": "animal", "2": "person", "3": "vehicle"},
             "Standard output requires MegaDetector animal/person/vehicle categories")
    _require(report.get("complete") is True and not report.get("errors") and report.get("draw") is True
             and report.get("subfolder") is True and report.get("sort_policy") == "highest_confidence",
             "Standard output requires complete drawn category output with highest-confidence routing")
    rows = report.get("images", [])
    _require(len(rows) == len(names) and {row.get("source_file") for row in rows} == names,
             "Box report/source identity set differs")
    files, routed = {}, {}
    for row in rows:
        source = row["source_file"]
        output = _relative(row["output_file"])
        parts = PurePosixPath(output).parts
        _require(len(parts) >= 3 and parts[0] == "boxed" and parts[1] in set(DIRECTORIES) - {"snips"},
                 "Box output has an unrecognized image category")
        category = parts[1]
        _require(PurePosixPath(*parts[2:]).as_posix() == source, "Box output/source identity differs")
        _require(row.get("status") == ("copied" if category == "blank" else "rendered"),
                 "Expected rendered detections or an unchanged blank image")
        local = category + "/" + PurePosixPath(source).name
        name = _join(leaf_map[source], local)
        _require(name not in files, "Finished image filename collision")
        path = _file(paths["box"] / output)
        if "output_sha256" in row:
            _require(_sha(path) == row["output_sha256"], "Box output differs from its report")
        files[name], routed[source] = path, local
    _require(set(_inventory(paths["box"] / "boxed")[0]) ==
             {row["output_file"].removeprefix("boxed/") for row in rows} | {"box_report.json"},
             "Unexpected files in boxed output")
    _require(crops.get("complete") is True and not crops.get("errors") and isinstance(crops.get("crops"), list),
             "Crop manifest is incomplete")
    crop_files, crop_keys = {}, {}
    for row in crops["crops"]:
        crop = _relative(row["crop_id"])
        _require(row["crop_file"] == crop and crop not in crop_files, "Invalid/duplicate crop identity")
        source, index = row["source_file"], row["detection_index"]
        detections = images.get(source, {}).get("detections", [])
        _require(type(index) is int and 0 <= index < len(detections)
                 and str(detections[index]["category"]) == "1", "Invalid animal crop/source identity")
        _require((source, index) not in crop_keys.values(), "Duplicate source/detection crop identity")
        crop_files[crop] = _file(paths["snip"] / "snips" / crop)
        crop_keys[crop] = source, index
    _require(set(_inventory(paths["snip"] / "snips")[0]) == set(crop_files) | {"crop_manifest.json"},
             "Unexpected files in snip output")
    artifacts = {str(p): {"sha256": _sha(p), "size": p.stat().st_size}
                 for p in (detector_path, crop_manifest_path, box_report_path, csv_path, *files.values(), *crop_files.values())}
    return detector, csv_path, files, routed, crop_files, crop_keys, artifacts, leaf_map


def _tables(path, routed, crop_keys, random_names, leaf_map):
    with _file(path).open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    required = {"crop_id", "filename", "rand_name", "source_file", "detection_index", "metadata_status"}
    _require(fields is not None and len(fields) == len(set(fields)) and required.issubset(fields),
             "Final EXIF-enriched prediction CSV lacks identity/metadata columns")
    seen, grouped = set(), {leaf: [] for leaf in leaf_map.values()}
    for row in rows:
        _require(None not in row and all(value is not None for value in row.values()), "Malformed final prediction CSV")
        crop = row["crop_id"]
        _require(crop in crop_keys and row["filename"] == crop and row["rand_name"] == crop,
                 "Final prediction CSV differs from immutable crop identities")
        source, index = crop_keys[crop]
        _require(row["source_file"] == source and row["detection_index"] == str(index)
                 and row["metadata_status"] == "exported", "Final prediction metadata/source identity differs")
        seen.add(crop)
        leaf = leaf_map[source]
        row["crop_id"] = _crop_local(crop, leaf)
        row["filename"] = row["crop_id"]
        row["rand_name"] = random_names[crop]
        row["source_file"] = routed[source]
        if "crop_file" in row:
            _require(row["crop_file"] == crop, "CSV crop_file differs from crop manifest")
            row["crop_file"] = "snips/" + random_names[crop]
        grouped[leaf].append(row)
    _require(seen == set(crop_keys), "Final CSV/crop identity set differs")
    outputs = {}
    for leaf, leaf_rows in grouped.items():
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(leaf_rows)
        outputs[_join(leaf, "mewc_out.csv")] = stream.getvalue().encode()
    return outputs


def _check_tree(target, receipt, *, partial=False):
    expected = receipt.get("files")
    leaves = receipt.get("leaves")
    _require(isinstance(expected, dict) and isinstance(receipt.get("directories"), list)
             and isinstance(leaves, list) and leaves and len(leaves) == len(set(leaves)), "Malformed result receipt")
    for leaf in leaves:
        _require(isinstance(leaf, str), "Malformed result leaf")
        if leaf:
            _relative(leaf)
        _require(not any(other != leaf and (not leaf or other.startswith(leaf + "/")) for other in leaves),
                 "Receipt mixes branch and leaf outputs")
    for name in expected:
        _relative(name)
        matching = [leaf for leaf in leaves if not leaf or name.startswith(leaf + "/")]
        _require(len(matching) == 1, "Receipt file does not belong to one leaf")
        leaf = matching[0]
        local = name[len(leaf) + 1:] if leaf else name
        parts = PurePosixPath(local).parts
        _require(local in {"md_out.json", "mewc_out.csv"}
                 or len(parts) == 2 and parts[0] in DIRECTORIES, "Receipt contains a nonstandard leaf result layout")
    _require(all(_join(leaf, table) in expected for leaf in leaves for table in ("md_out.json", "mewc_out.csv")),
             "Receipt is missing leaf result tables")
    _require(receipt["directories"] == _parents(expected, leaves), "Receipt directory layout differs")
    _no_case_collisions([*expected, *receipt["directories"]])
    files, directories = _inventory(target)
    if partial:
        _require(set(files).issubset(expected) and directories.issubset(set(receipt["directories"])),
                 "Unexpected files or directories in partial standard output")
    else:
        _require(files == expected and directories == set(receipt["directories"]),
                 "Standard output differs from its external receipt")


def verify(target, receipt):
    """Verify a published or transferred tree against its private receipt."""
    _require(receipt.get("schema_version") == 1 and receipt.get("format") == FORMAT
             and receipt.get("complete") is True, "Standard result receipt is incomplete or unsupported")
    _check_tree(_path(target), receipt)
    return {"complete": True, "files": len(receipt["files"]), "format": FORMAT,
            "fingerprint": receipt["fingerprint"]}


def materialize(target, *, stages, sources, fingerprint, receipt_path, guard=None,
                rename_snips=True, snip_chars=16):
    """Build/resume an ordinary result tree; caller atomically publishes it.

    ``stages`` and ``sources`` must come from the controller's successful
    verification. ``receipt_path`` must be outside ``target``; retries require
    that same receipt. No pipeline artifact or supplied source is modified.
    """
    target, receipt_path = _path(target), _path(receipt_path)
    _require(not receipt_path.is_relative_to(target), "Result receipt must remain outside the delivered tree")
    _require(type(rename_snips) is bool and type(snip_chars) is int and 8 <= snip_chars <= 128,
             "Invalid result snip naming settings")
    _require(isinstance(fingerprint, str) and fingerprint, "Verified run fingerprint is required")
    detector, csv_path, files, routed, crops, crop_keys, artifacts, leaf_map = _load_sources(stages, sources)
    leaves = sorted(set(leaf_map.values()))
    for source_root in {Path(stage) for stage in stages.values()}:
        _require(not target.is_relative_to(source_root) and not source_root.is_relative_to(target),
                 "Standard output must be separate from stage artifacts")
        _require(not receipt_path.is_relative_to(source_root),
                 "Result receipt must not modify immutable stage artifacts")
    identity = {"fingerprint": fingerprint, "partial_destination": str(target),
                "rename_snips": rename_snips, "snip_chars": snip_chars,
                "sources_digest": hashlib.sha256(_canonical({"sources": sources, "artifacts": artifacts})).hexdigest()}
    previous = _json(receipt_path) if receipt_path.exists() else None
    _require(not target.exists() or previous is not None,
             "Existing partial standard output has no external ownership receipt")
    if previous is not None:
        _require(previous.get("schema_version") == 1 and previous.get("format") == FORMAT
                 and previous.get("identity") == identity, "Standard output receipt does not belong to this run/destination")
        random_names = previous.get("random_names", {})
        _require(set(random_names) == set(crops), "Snip filename receipt/crop identity set differs")
    else:
        random_names, used = {}, set()
        alphabet = string.ascii_letters + string.digits
        for crop in sorted(crops):
            if rename_snips:
                for _ in range(100):
                    name = "".join(secrets.choice(alphabet) for _ in range(snip_chars)) + PurePosixPath(crop).suffix
                    if name.casefold() not in used:
                        break
                else:
                    raise Invalid("Cannot allocate a unique random snip filename")
            else:
                name = _crop_local(crop, leaf_map[crop_keys[crop][0]])
            key = name.casefold() if rename_snips else _join(leaf_map[crop_keys[crop][0]], name).casefold()
            _require(key not in used, "Exported snip filename collision")
            random_names[crop] = name
            used.add(key)
    snip_destinations = [_join(leaf_map[crop_keys[crop][0]], name) for crop, name in random_names.items()]
    _require(len(set(snip_destinations)) == len(crops), "Duplicate exported snip filename")
    _no_case_collisions(snip_destinations)
    if rename_snips:
        _require(len({name.casefold() for name in random_names.values()}) == len(crops),
                 "Duplicate case-insensitive random snip filename")
    for crop, name in random_names.items():
        _relative(name)
        if rename_snips:
            suffix = PurePosixPath(crop).suffix
            token = name[:-len(suffix)] if suffix else name
            _require("/" not in name and len(token) == snip_chars
                     and all(c in string.ascii_letters + string.digits for c in token)
                     and name == token + suffix, "Invalid random snip filename in receipt")
        else:
            _require(name == _crop_local(crop, leaf_map[crop_keys[crop][0]]), "Nonrandom snip filename differs")
        files[_join(leaf_map[crop_keys[crop][0]], "snips/" + name)] = crops[crop]
    detector.pop("mewc_path_rebase", None)
    generated = _tables(csv_path, routed, crop_keys, random_names, leaf_map)
    for leaf in leaves:
        document = {key: value for key, value in detector.items() if key != "images"}
        document["images"] = [{**image, "file": routed[image["file"]]}
                              for image in detector["images"] if leaf_map[image["file"]] == leaf]
        generated[_join(leaf, "md_out.json")] = _canonical(document)
    expected = {name: dict(artifacts[str(path)]) for name, path in files.items()}
    expected.update({name: {"sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}
                     for name, payload in generated.items()})
    receipt = {"schema_version": 1, "format": FORMAT, "complete": False, "fingerprint": fingerprint,
               "destination": str(target), "rename_snips": rename_snips, "snip_chars": snip_chars,
               "identity": identity, "random_names": random_names, "files": expected, "leaves": leaves,
               "directories": _parents(expected, leaves)}
    _require(not set(expected) & set(receipt["directories"]), "Result filename/directory collision")
    _no_case_collisions([*expected, *receipt["directories"]])
    if previous is not None:
        _require(all(previous.get(key) == receipt[key] for key in ("files", "directories", "random_names")),
                 "Standard output contents differ from the existing receipt")
    # Persist both ownership and the stable random mapping before creating the tree.
    _assert_artifacts(artifacts)
    _atomic_bytes(receipt_path, _canonical(receipt), guard)
    if target.exists():
        _check_tree(target, receipt, partial=True)
    else:
        _mkdir(target, guard)
    for directory in receipt["directories"]:
        _mkdir(target / directory, guard)
    for name, path in files.items():
        destination = target / name
        if destination.is_file() and _sha(destination) == expected[name]["sha256"]:
            continue
        _copy(path, destination, guard)
    for name, payload in generated.items():
        destination = target / name
        if destination.is_file() and _sha(destination) == expected[name]["sha256"]:
            continue
        _atomic_bytes(destination, payload, guard)
    _check_tree(target, receipt)
    _assert_artifacts(artifacts)
    receipt["complete"] = True
    _atomic_bytes(receipt_path, _canonical(receipt), guard)
    verify(target, receipt)
    return receipt
