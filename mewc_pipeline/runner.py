"""Run immutable stage images against a verified dataset and model snapshot.

The CLI runs on the prepared host. SSH/rsync provide transport; no daemon or
cloud credentials are needed here. Completion requires content and identities,
not a successful container exit alone.
"""

import argparse
import csv
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

STAGES = ("detect", "snip", "predict", "exif", "box")
POLICY = "category-confidence-v1"
IMAGE = re.compile(r"(?:[a-zA-Z0-9._:/-]+@)?sha256:[a-f0-9]{64}\Z")
OPTIONS = dict(
    THRESHOLD=0.01,
    LOWER_CONF=0.05,
    UPPER_CONF=0.9,
    OVERLAP=0.3,
    EDGE_DIST=0.02,
    MIN_EDGES=0,
    SNIP_SIZE=600,
    BATCH_SIZE=16,
    DRAW=True,
    TOP_CLASSES=True,
)
ENV_COMMON = dict(
    PYTHONUNBUFFERED="1",
    INPUT_DIR="/images",
    MD_FILE="md_out.json",
    SNIP_DIR="snips",
    PRED_FILE="mewc_out.pkl",
    PRED_CSV="mewc_out.csv",
    EN_FILE="mewc_out.pkl",
    EN_CSV="mewc_out.csv",
    METADATA_DIR="metadata",
    EXIF_DIR="camelot",
    OUTPUT_DIR="boxed",
    SUPPRESSION_POLICY=POLICY,
    SORT_POLICY="mixed",
    SUBFOLDER=True,
    RENAME_SNIPS=False,
    RECURSIVE=True,
    RELATIVE_FILENAMES=True,
    USE_SAVEDMODEL=False,
    SAFE_MODE=True,
    PRINT_SUMMARY=False,
    KERAS_BACKEND="tensorflow",
    XLA_JIT="auto",
    HOME="/tmp",
    TMPDIR="/images/.tmp",
    MPLCONFIGDIR="/tmp/matplotlib",
    YOLO_CONFIG_DIR="/tmp/ultralytics",
    TF_NUM_INTRAOP_THREADS="4",
    TF_NUM_INTEROP_THREADS="2",
)


class Invalid(ValueError):
    pass


def require(value, message):
    if not value:
        raise Invalid(message)


def read(path):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, mode="wb", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def relative(value):
    require(
        isinstance(value, str) and value and "\\" not in value,
        "Expected POSIX relative path",
    )
    path = PurePosixPath(value)
    require(
        not path.is_absolute()
        and all(p not in ("", ".", "..") for p in value.split("/")),
        f"Unsafe relative path: {value!r}",
    )
    return path.as_posix()


def absolute(value, label):
    require(
        isinstance(value, str) and Path(value).is_absolute(),
        f"{label} must be an absolute path",
    )
    path = Path(value)
    require(
        not any(p.is_symlink() for p in (path, *path.parents)),
        f"{label} cannot contain symlinks",
    )
    return path.resolve()


def inventory(root, include_mtime=False):
    """Hash every regular file; reject links and devices, including hidden files."""
    require(root.is_dir(), f"Missing directory: {root}")
    result = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), f"Symlink is unsupported: {path}")
        if path.is_dir():
            continue
        require(path.is_file(), f"Non-regular file: {path}")
        name = relative(path.relative_to(root).as_posix())
        result[name] = {"sha256": sha(path), "size": path.stat().st_size}
        if include_mtime:
            result[name]["mtime_ns"] = path.stat().st_mtime_ns
    return result


def config(path):
    cfg = read(path)
    fields = {
        "schema_version",
        "run_id",
        "input_dir",
        "run_dir",
        "images",
        "model",
        "options",
        "gpu",
        "cpus",
        "memory",
        "timeout_seconds",
        "storage_mount",
        "storage_uuid",
    }
    require(
        isinstance(cfg, dict) and not set(cfg) - fields, "Unknown configuration fields"
    )
    require(cfg.get("schema_version") == 1, "schema_version must be 1")
    require(
        re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", cfg.get("run_id", "")),
        "Invalid run_id",
    )
    absolute(cfg.get("storage_mount"), "storage_mount")
    require(
        isinstance(cfg.get("storage_uuid"), str)
        and re.fullmatch(r"[A-Fa-f0-9-]{8,64}", cfg["storage_uuid"]),
        "storage_uuid must identify the retained filesystem",
    )
    source = absolute(cfg.get("input_dir"), "input_dir")
    run = absolute(cfg.get("run_dir"), "run_dir")
    require(
        source != run
        and not source.is_relative_to(run)
        and not run.is_relative_to(source),
        "input_dir and run_dir must be separate trees",
    )
    require(
        set(cfg.get("images", {})) == set(STAGES),
        "Five exact stage images are required",
    )
    require(
        all(isinstance(v, str) and IMAGE.fullmatch(v) for v in cfg["images"].values()),
        "Images must use an OCI digest or immutable local sha256 image ID",
    )
    model = cfg.get("model")
    require(
        isinstance(model, dict)
        and set(model) == {"detector", "classifier", "class_map", "manifest"},
        "model requires detector, classifier, class_map and manifest paths",
    )
    for key, value in model.items():
        require(absolute(value, key).is_file(), f"Missing model asset: {key}")
    supplied = cfg.get("options", {})
    require(
        isinstance(supplied, dict) and not set(supplied) - set(OPTIONS),
        "Unknown scientific options",
    )
    opts = {**OPTIONS, **supplied}
    for key in ("DRAW", "TOP_CLASSES"):
        require(type(opts[key]) is bool, f"{key} must be a JSON boolean")
    for key in ("THRESHOLD", "LOWER_CONF", "UPPER_CONF", "OVERLAP", "EDGE_DIST"):
        value = opts[key]
        require(
            type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1,
            f"{key} must be a finite probability/distance in [0,1]",
        )
    require(
        opts["LOWER_CONF"] <= opts["UPPER_CONF"],
        "Confidence bounds are inconsistent",
    )
    for key in ("MIN_EDGES", "SNIP_SIZE", "BATCH_SIZE"):
        require(
            type(opts[key]) is int and opts[key] >= (0 if key == "MIN_EDGES" else 1),
            f"Invalid {key}",
        )
    require(opts["MIN_EDGES"] <= 4, "MIN_EDGES exceeds four box edges")
    cfg["options"] = opts
    for key, default in (
        ("gpu", True),
        ("cpus", 8),
        ("memory", "32g"),
        ("timeout_seconds", 86400),
    ):
        cfg.setdefault(key, default)
    require(type(cfg["gpu"]) is bool, "gpu must be a JSON boolean")
    for key in ("cpus", "timeout_seconds"):
        require(
            type(cfg[key]) is int and cfg[key] > 0, f"{key} must be a positive integer"
        )
    require(
        isinstance(cfg["memory"], str)
        and re.fullmatch(r"[1-9][0-9]*[mg]", cfg["memory"]),
        "Invalid memory limit",
    )
    return cfg


def model_contract(cfg):
    model = cfg["model"]
    manifest = read(model["manifest"])
    require(manifest.get("schema_version") == 1, "Unsupported model manifest")
    require(
        manifest.get("model_sha256") == sha(model["classifier"]),
        "Classifier hash differs from model manifest",
    )
    require(
        manifest.get("class_map_sha256") == sha(model["class_map"]),
        "Class map hash differs from model manifest",
    )
    require(
        isinstance(manifest.get("class_order"), list) and manifest["class_order"],
        "Model must declare class order",
    )
    require(
        len(set(manifest["class_order"])) == len(manifest["class_order"]),
        "Duplicate model class names",
    )
    require(
        isinstance(manifest.get("architecture"), str), "Missing declared architecture"
    )
    require(
        manifest.get("preprocessing") is not None
        and manifest.get("input_shape") is not None,
        "Model must declare input shape and preprocessing",
    )
    return manifest


def image_files(source):
    files = inventory(source, include_mtime=True)
    require(files, "Input dataset is empty")
    require(
        all(Path(name).suffix.lower() in {".jpg", ".jpeg"} for name in files),
        "Full Camelot pipeline currently requires JPEG inputs only; stage other files separately",
    )
    return files


def snapshot(source, destination, expected, guard=lambda: None):
    """Resumable per-file copies, accepting only hashes from the frozen inventory."""
    guard()
    destination.mkdir(parents=True, exist_ok=True)
    for name, record in expected.items():
        guard()
        src, dst = source / name, destination / name
        require(
            src.is_file()
            and not src.is_symlink()
            and sha(src) == record["sha256"]
            and (
                "mtime_ns" not in record or src.stat().st_mtime_ns == record["mtime_ns"]
            ),
            f"Source changed during staging: {name}",
        )
        if (
            dst.is_file()
            and sha(dst) == record["sha256"]
            and (
                "mtime_ns" not in record or dst.stat().st_mtime_ns == record["mtime_ns"]
            )
        ):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        require(not dst.is_symlink(), f"Symlink at snapshot destination: {name}")
        absolute(str(dst), "snapshot destination")
        tmp = absolute(
            str(dst.with_name("." + dst.name + ".partial")), "partial snapshot file"
        )
        shutil.copy2(src, tmp)
        require(sha(tmp) == record["sha256"], f"Copy verification failed: {name}")
        os.replace(tmp, dst)
    require(
        inventory(
            destination,
            include_mtime=any("mtime_ns" in row for row in expected.values()),
        )
        == expected,
        "Snapshot contains unexpected or changed files",
    )


def validate_detection(path, expected):
    data = read(path)
    require(
        isinstance(data, dict) and isinstance(data.get("images"), list),
        "Invalid detector document",
    )
    seen = set()
    for item in data["images"]:
        name = relative(item.get("file"))
        require(name not in seen, f"Duplicate detector image: {name}")
        seen.add(name)
        require(not item.get("failure"), f"Detector failed image: {name}")
        dets = item.get("detections")
        require(isinstance(dets, list), f"Missing detections list: {name}")
        for det in dets:
            require(
                det.get("category") in ("1", "2", "3"),
                f"Invalid detector category: {name}",
            )
            confidence = det.get("conf")
            require(
                type(confidence) in (int, float)
                and math.isfinite(confidence)
                and 0 <= confidence <= 1,
                "Invalid detection confidence",
            )
            box = det.get("bbox")
            require(
                isinstance(box, list)
                and len(box) == 4
                and all(type(v) in (int, float) and math.isfinite(v) for v in box)
                and 0 <= box[0] <= 1
                and 0 <= box[1] <= 1
                and box[2] > 0
                and box[3] > 0
                and box[0] + box[2] <= 1.000001
                and box[1] + box[3] <= 1.000001,
                "Invalid detection box",
            )
    require(seen == set(expected), "Detector identity set differs from input snapshot")
    return data


def validate_crops(root, data, sources):
    doc = read(root / "snips/crop_manifest.json")
    require(
        doc.get("schema_version") == 1
        and doc.get("complete") is True
        and not doc.get("errors"),
        "Crop manifest is incomplete",
    )
    require(
        doc.get("policy") == POLICY and doc.get("selection") == "animal",
        "Unexpected crop policy",
    )
    expected_all = {
        (im["file"], i) for im in data["images"] for i in range(len(im["detections"]))
    }
    accounted, crops, names = set(), {}, set()
    for row in doc["crops"]:
        key = (relative(row["source_file"]), row["detection_index"])
        require(
            type(key[1]) is int and key in expected_all and key not in accounted,
            "Invalid/duplicate crop identity",
        )
        name = relative(row["crop_file"])
        require(
            row["crop_id"] == name and name not in names, "Invalid/duplicate crop ID"
        )
        require((root / "snips" / name).is_file(), f"Missing crop: {name}")
        accounted.add(key)
        names.add(name)
        crops[name] = key
    for row in doc["omissions"]:
        key = (relative(row["source_file"]), row["detection_index"])
        require(
            type(key[1]) is int
            and key in expected_all
            and key not in accounted
            and row.get("reason"),
            "Invalid/duplicate omission identity",
        )
        accounted.add(key)
    require(
        accounted == expected_all,
        "A detector identity has neither a crop nor an explicit omission",
    )
    require(
        {row["source_file"] for row in doc["images"]} == set(sources)
        and len(doc["images"]) == len(sources)
        and all(row["status"] == "complete" for row in doc["images"]),
        "Incomplete crop image accounting",
    )
    actual = set(inventory(root / "snips")) - {"crop_manifest.json"}
    require(actual == names, "Unexpected crop files")
    return crops


def validate_predictions(root, crops, declaration, top):
    marker = read(root / "prediction_manifest.json")
    require(
        marker.get("schema_version") == 1 and marker.get("complete") is True,
        "Prediction marker incomplete",
    )
    require(
        set(marker.get("outputs", {}))
        == {"mewc_out.csv", "mewc_out.pkl", "prediction_scores.npz"},
        "Unexpected prediction outputs",
    )
    for name, checksum in marker["outputs"].items():
        require(sha(root / name) == checksum, f"Prediction output changed: {name}")
    with (root / "mewc_out.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    seen, labels = set(), set()
    names = declaration["class_order"]
    for row in rows:
        crop = row["crop_id"]
        require(
            crop in crops
            and (row["source_file"], int(row["detection_index"])) == crops[crop],
            "Prediction identity differs from crop manifest",
        )
        require(row["rand_name"] == crop, "Prediction renamed a crop")
        prob = float(row["prob"])
        require(
            math.isfinite(prob) and 0 <= prob <= 1, "Invalid classification probability"
        )
        class_index = int(row["class_index"])
        require(
            0 <= class_index < len(names)
            and row["class_name"] == names[class_index]
            and row["class_id"] == str(declaration["class_ids"][class_index]),
            "Prediction class axis/code/name differs",
        )
        require(
            not top or row["class_rank"] == "1",
            "Top-only table contains a lower-ranked class",
        )
        key = (crop, row["class_id"])
        require(
            key not in labels and int(row["class_rank"]) >= 1,
            "Duplicate/invalid prediction class",
        )
        labels.add(key)
        seen.add(crop)
    require(seen == set(crops), "Prediction/crop identity mismatch")
    return rows


def validate_report(path, sources, statuses):
    report = read(path)
    require(
        report.get("schema_version") == 1
        and report.get("complete") is True
        and not report.get("errors"),
        f"Incomplete report: {path.name}",
    )
    rows = report["images"]
    require(
        len(rows) == len(sources) and {r["source_file"] for r in rows} == set(sources),
        f"Incomplete image identities: {path.name}",
    )
    require(all(r["status"] in statuses for r in rows), f"Failed image in {path.name}")
    return report


class Pipeline:
    def __init__(self, cfg):
        self.cfg = cfg
        self.root = Path(cfg["run_dir"])
        self.manifest_path = self.root / "manifest.json"
        self.storage()
        if self.root.exists() and not self.manifest_path.exists():
            require(not any(self.root.iterdir()), "Unregistered run_dir must be empty")
        self.root.mkdir(parents=True, exist_ok=True)
        self.active = None

    def storage(self):
        mount = absolute(self.cfg["storage_mount"], "storage_mount")
        require(
            mount != Path("/")
            and mount.is_mount()
            and mount.stat().st_dev != Path("/").stat().st_dev,
            "Retained storage is not a separate mounted filesystem",
        )
        require(
            self.root.is_relative_to(mount), "run_dir must be beneath storage_mount"
        )
        observed = subprocess.run(
            [
                "findmnt",
                "--noheadings",
                "--raw",
                "--mountpoint",
                str(mount),
                "--output",
                "UUID",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        require(
            observed.lower() == self.cfg["storage_uuid"].lower(),
            "Retained filesystem UUID differs from configuration",
        )

    def save(self, manifest):
        self.storage()
        atomic(self.manifest_path, manifest)

    def lock(self):
        path = absolute("/tmp/mewc-pipeline.lock", "host pipeline lock")
        stream = path.open("a")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.close()
            raise Invalid("Another pipeline is running under this run parent") from None
        return stream

    def preflight(self):
        self.storage()
        declaration = model_contract(self.cfg)
        for stage, image in self.cfg["images"].items():
            result = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
                text=True,
                check=True,
            )
            details = json.loads(result.stdout)[0]
            require(
                details["Os"] == "linux" and details["Architecture"] == "amd64",
                f"{stage} must be Linux amd64",
            )
        info = json.loads(
            subprocess.run(
                ["docker", "info", "--format", "{{json .}}"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        require(
            Path(info["DockerRootDir"])
            .resolve()
            .is_relative_to(Path(self.cfg["storage_mount"])),
            "Docker storage must be on the verified retained filesystem",
        )
        self.runtime = {
            "docker_version": info["ServerVersion"],
            "docker_root": info["DockerRootDir"],
            "python_version": sys.version.split()[0],
        }

        if self.cfg["gpu"]:
            gpu = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,uuid,driver_version",
                    "--format=csv,noheader",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            self.runtime["gpu"] = gpu.stdout.strip().splitlines()
        files = image_files(Path(self.cfg["input_dir"]))
        return declaration, files

    def stage(self):
        declaration, files = self.preflight()
        assets = {
            key: {"sha256": sha(value), "size": Path(value).stat().st_size}
            for key, value in self.cfg["model"].items()
        }
        identity = {
            "config": self.cfg,
            "inputs": files,
            "models": assets,
            "model_declaration": declaration,
            "runner_sources": {
                p.name: sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))
            },
        }
        if self.manifest_path.exists():
            manifest = read(self.manifest_path)
            require(
                manifest["identity"] == identity,
                "Run identity changed; choose a new run_dir and run_id",
            )
        else:
            manifest = dict(
                schema_version=1,
                identity=identity,
                fingerprint=digest(identity),
                created_at=now(),
                host=socket.gethostname(),
                runtime=getattr(self, "runtime", {}),
                state="staging",
                stages={},
            )
            self.save(manifest)
        snapshot(
            Path(self.cfg["input_dir"]), self.root / "inputs", files, guard=self.storage
        )
        for key, value in self.cfg["model"].items():
            snapshot(
                Path(value).parent,
                self.root / "models" / key,
                {Path(value).name: assets[key]},
                guard=self.storage,
            )
        manifest["state"] = "staged"
        self.save(manifest)
        return manifest

    def model_path(self, key):
        return self.root / "models" / key / Path(self.cfg["model"][key]).name

    def cleanup(self, name):
        inspect = subprocess.run(
            ["docker", "container", "inspect", name], capture_output=True, text=True
        )
        if inspect.returncode == 0:
            details = json.loads(inspect.stdout)[0]
            require(
                details.get("Config", {}).get("Labels", {}).get("mewc.run")
                == digest(self.cfg),
                "Container name belongs to another run; refusing cleanup",
            )
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name=^/{name}$",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        require(
            name not in result.stdout.splitlines(),
            "Container cleanup failed; inspect before resuming",
        )

    def container(self, stage, attempt, mounts, env, extra=None):
        self.storage()
        name = f"mewc-{self.cfg['run_id']}-{digest(self.cfg)[:16]}-{stage}"
        self.cleanup(name)
        cmd = [
            "docker",
            "run",
            "--name",
            name,
            "--label",
            f"mewc.run={digest(self.cfg)}",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "1024",
            "--cpus",
            str(self.cfg["cpus"]),
            "--memory",
            self.cfg["memory"],
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--tmpfs",
            "/tmp:rw,nosuid,size=2g",
            "--mount",
            f"type=bind,src={attempt},dst=/images" + (",readonly" if extra else ""),
        ]
        if self.cfg["gpu"] and stage in ("detect", "predict") and not extra:
            cmd += ["--gpus", "all"]
        for source, target in mounts:
            require(
                source.exists() and not source.is_symlink(),
                f"Missing/unsafe stage input: {source}",
            )
            require("," not in str(source), "Docker mount source cannot contain commas")
            cmd += ["--mount", f"type=bind,src={source},dst={target},readonly"]
        for key, value in sorted(env.items()):
            cmd += ["--env", f"{key}={value}"]
        if extra:
            cmd += ["--entrypoint", "python"]
        cmd += [self.cfg["images"][stage]] + (extra or [])
        log = self.root / "logs" / f"{stage}-{attempt.name}-{time.time_ns()}.log"
        log.parent.mkdir(exist_ok=True)
        self.active = name
        try:
            with log.open("w") as output:
                result = subprocess.run(
                    cmd,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=self.cfg["timeout_seconds"],
                )
            require(
                result.returncode == 0,
                f"{stage} failed (exit {result.returncode}); see {log}",
            )
        finally:
            self.cleanup(name)
            self.active = None

    def paths(self, manifest):
        return {
            stage: self.root / item["directory"]
            for stage, item in manifest["stages"].items()
            if item.get("state") == "complete"
        }

    def validate(self, stage, folder, paths, sources):
        if stage == "detect":
            validate_detection(folder / "md_out.json", sources)
        else:
            data = validate_detection(paths["detect"] / "md_out.json", sources)
            crops = validate_crops(
                folder if stage == "snip" else paths["snip"], data, sources
            )
            if stage == "predict":
                declaration = read(self.model_path("manifest"))
                marker = read(folder / "prediction_manifest.json")
                require(
                    marker.get("crop_manifest_sha256")
                    == sha(paths["snip"] / "snips/crop_manifest.json"),
                    "Prediction crop provenance differs",
                )
                require(
                    marker.get("model_manifest_sha256")
                    == sha(self.model_path("manifest")),
                    "Prediction model provenance differs",
                )
                validate_predictions(
                    folder, crops, declaration, self.cfg["options"]["TOP_CLASSES"]
                )
                self.check_predictions(folder, paths)
            elif stage == "exif":
                report = validate_report(
                    folder / "metadata/metadata.json",
                    sources,
                    {"exported", "no_animal"},
                )
                for row in report["images"]:
                    source = row["source_file"].removeprefix("originals/")
                    require(
                        row["source_sha256"] == sources[row["source_file"]]["sha256"],
                        "Metadata source hash changed",
                    )
                    require(
                        (folder / "camelot" / "originals" / source).is_file(),
                        "Missing Camelot review copy",
                    )
            elif stage == "box":
                report = validate_report(
                    folder / "boxed/box_report.json", sources, {"rendered", "copied"}
                )
                for row in report["images"]:
                    require(
                        (folder / row["output_file"]).is_file(),
                        "Missing boxed review copy",
                    )

    def check_predictions(self, folder, paths):
        # No unpickling in the host/controller process. The locked, isolated
        # classifier image validates full scores, identity, CSV and pickle.
        mounts = [
            (paths["snip"] / "snips", "/images/snips"),
            (self.model_path("class_map"), "/models/class_map.yaml"),
            (self.model_path("manifest"), "/models/manifest.json"),
            (
                Path(__file__).with_name("validate_predictions.py"),
                "/validate_predictions.py",
            ),
        ]
        env = {**ENV_COMMON, **self.cfg["options"]}
        self.container("predict", folder, mounts, env, ["/validate_predictions.py"])

    def run(self):
        # A killed controller can leave a later stage alive even when an earlier
        # artifact needs rebuilding. Reconcile every exact owned name first.
        self.storage()
        for stage in STAGES:
            self.cleanup(f"mewc-{self.cfg['run_id']}-{digest(self.cfg)[:16]}-{stage}")
        manifest = self.stage()
        sources = {
            "originals/" + name: row
            for name, row in manifest["identity"]["inputs"].items()
        }
        paths = self.paths(manifest)
        invalidated = False
        for stage in STAGES:
            previous = manifest["stages"].get(stage, {})
            if not invalidated and previous.get("state") == "complete":
                folder = paths[stage]
                try:
                    require(
                        inventory(folder) == previous["outputs"],
                        f"{stage} content changed",
                    )
                    self.validate(stage, folder, paths, sources)
                    print(f"{stage}: reusing verified output", flush=True)
                    continue
                except (Invalid, OSError, KeyError, ValueError):
                    invalidated = True
            else:
                invalidated = True
            if invalidated:
                for downstream in STAGES[STAGES.index(stage) :]:
                    paths.pop(downstream, None)
                    if downstream in manifest["stages"]:
                        manifest["stages"][downstream]["state"] = "stale"
            self.storage()
            attempts = self.root / "work" / stage
            attempts.mkdir(parents=True, exist_ok=True)
            attempt = attempts / f"attempt-{time.time_ns()}"
            attempt.mkdir()
            (attempt / ".tmp").mkdir()
            record = dict(
                state="running",
                directory=attempt.relative_to(self.root).as_posix(),
                started_at=now(),
            )
            manifest["stages"][stage] = record
            manifest["state"] = "running"
            self.save(manifest)
            print(f"{stage}: started", flush=True)
            env = {**ENV_COMMON, **self.cfg["options"]}
            mounts = [(self.root / "inputs", "/images/originals")]
            if stage == "detect":
                env.update(
                    INPUT_DIR="/images/originals",
                    OUTPUT_DIR="/images",
                    MD_MODEL="/models/detector.pt",
                )
                mounts.append((self.model_path("detector"), "/models/detector.pt"))
            else:
                mounts.append((paths["detect"] / "md_out.json", "/images/md_out.json"))
            if stage in ("predict", "exif"):
                mounts.append((paths["snip"] / "snips", "/images/snips"))
            if stage == "predict":
                env.update(
                    MODEL=manifest["identity"]["model_declaration"]["architecture"],
                    MODEL_PATH="/models/model.keras",
                    CLASS_MAP_PATH="/models/class_map.yaml",
                    MODEL_MANIFEST_PATH="/models/manifest.json",
                )
                mounts += [
                    (self.model_path(key), target)
                    for key, target in [
                        ("classifier", "/models/model.keras"),
                        ("class_map", "/models/class_map.yaml"),
                        ("manifest", "/models/manifest.json"),
                    ]
                ]
            if stage == "exif":
                mounts += [
                    (paths["predict"] / name, "/images/" + name)
                    for name in ("mewc_out.pkl", "mewc_out.csv")
                ]
            if stage == "box":
                mounts[0] = (paths["exif"] / "camelot/originals", "/images/originals")
            try:
                self.verify_models(manifest)
                self.container(stage, attempt, mounts, env)
                if stage == "detect":
                    original = attempt / "md_out.json"
                    data = validate_detection(original, manifest["identity"]["inputs"])
                    original.rename(attempt / "md_raw.json")
                    for item in data["images"]:
                        item["file"] = "originals/" + item["file"]
                    data["mewc_path_rebase"] = {
                        "schema_version": 1,
                        "prefix": "originals/",
                        "original": "md_raw.json",
                    }
                    atomic(original, data)
                if stage == "snip":
                    mounts.append(
                        (
                            Path(__file__).with_name("validate_selection.py"),
                            "/validate_selection.py",
                        )
                    )
                    self.container(
                        stage, attempt, mounts, env, ["/validate_selection.py"]
                    )
                self.validate(stage, attempt, paths, sources)
                require(
                    inventory(self.root / "inputs", include_mtime=True)
                    == manifest["identity"]["inputs"],
                    "Input snapshot was modified",
                )
                record.update(
                    state="complete",
                    completed_at=now(),
                    outputs=inventory(attempt),
                    verified_images=len(sources),
                )
                if stage != "detect":
                    crop_dir = attempt if stage == "snip" else paths["snip"]
                    record["verified_crops"] = len(
                        read(crop_dir / "snips/crop_manifest.json")["crops"]
                    )
                if stage == "predict":
                    record["prediction_rows"] = read(
                        attempt / "prediction_manifest.json"
                    ).get("output_row_count")
                paths[stage] = attempt
                self.save(manifest)
                print(f"{stage}: verified", flush=True)
            except BaseException as error:
                record.update(state="failed", error=str(error), failed_at=now())
                manifest["state"] = "failed"
                self.save(manifest)
                raise
        manifest["state"] = (
            "success-empty"
            if not read(paths["snip"] / "snips/crop_manifest.json")["crops"]
            else "complete"
        )
        manifest["completed_at"] = now()
        self.save(manifest)
        return self.verify()

    def verify_models(self, manifest):
        for key, record in manifest["identity"]["models"].items():
            asset = self.model_path(key)
            require(
                inventory(asset.parent) == {asset.name: record},
                f"{key} model snapshot changed",
            )

    def verify(self):
        self.storage()
        manifest = read(self.manifest_path)
        require(
            manifest["identity"]["config"] == self.cfg,
            "Configuration differs from run identity",
        )
        require(
            manifest["identity"]["runner_sources"]
            == {p.name: sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))},
            "Runner sources differ from run identity; verify with the frozen checkout",
        )
        require(
            manifest["state"] in ("complete", "success-empty"), "Run is not complete"
        )
        require(
            inventory(self.root / "inputs", include_mtime=True)
            == manifest["identity"]["inputs"],
            "Input snapshot hash mismatch",
        )
        self.verify_models(manifest)
        paths = self.paths(manifest)
        sources = {
            "originals/" + name: row
            for name, row in manifest["identity"]["inputs"].items()
        }
        for stage in STAGES:
            require(stage in paths, f"Missing completed stage: {stage}")
            require(
                inventory(paths[stage]) == manifest["stages"][stage]["outputs"],
                f"{stage} output hash mismatch",
            )
            self.validate(stage, paths[stage], paths, sources)
        report = dict(
            schema_version=1,
            complete=True,
            verified_at=now(),
            fingerprint=manifest["fingerprint"],
            images=len(sources),
            crops=len(read(paths["snip"] / "snips/crop_manifest.json")["crops"]),
            state=manifest["state"],
        )
        self.storage()
        atomic(self.root / "verification.json", report)
        return report

    def export(self, target):
        verification = self.verify()
        target = absolute(str(target), "export directory")
        require(
            not target.exists(),
            "Export directory already exists; choose a fresh destination",
        )
        for protected in (self.root, Path(self.cfg["input_dir"])):
            require(
                not target.is_relative_to(protected)
                and not protected.is_relative_to(target),
                "Export must be separate from run and input trees",
            )
        manifest = read(self.manifest_path)
        paths = self.paths(manifest)
        self.storage()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = absolute(
            str(target.with_name(target.name + ".partial")), "partial export"
        )
        owner = {
            "schema_version": 1,
            "run_fingerprint": verification["fingerprint"],
            "destination": str(target),
        }
        owner_file = temporary / "export_identity.json"
        if temporary.exists():
            require(
                owner_file.is_file()
                and not owner_file.is_symlink()
                and read(owner_file) == owner,
                "Existing partial export does not belong to this run and destination",
            )
        else:
            temporary.mkdir()
            atomic(owner_file, owner)
        selections = {
            self.manifest_path: "manifest.json",
            self.root / "verification.json": "verification.json",
            paths["detect"] / "md_out.json": "md_out.json",
            paths["snip"] / "snips/crop_manifest.json": "crop_manifest.json",
            paths["predict"] / "prediction_manifest.json": "prediction_manifest.json",
        }
        for name in ("mewc_out.csv", "mewc_out.pkl", "prediction_scores.npz"):
            selections[paths["predict"] / name] = name
        for source, name in (
            (paths["exif"] / "metadata", "metadata"),
            (paths["exif"] / "camelot", "camelot"),
            (paths["box"] / "boxed", "review"),
            (self.root / "logs", "logs"),
        ):
            for relative_name in inventory(source):
                selections[source / relative_name] = name + "/" + relative_name
        # Construct expectations from verified sources, never from the copied destination.
        expected = {
            name: {"sha256": sha(source), "size": source.stat().st_size}
            for source, name in selections.items()
        }
        expected["export_identity.json"] = {
            "sha256": sha(owner_file),
            "size": owner_file.stat().st_size,
        }
        require(
            not set(inventory(temporary)) - set(expected) - {"export_manifest.json"},
            "Unexpected files in partial export",
        )
        (temporary / "export_manifest.json").unlink(missing_ok=True)
        for source, name in selections.items():
            self.storage()
            destination = temporary / name
            if destination.is_file() and sha(destination) == expected[name]["sha256"]:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            require(
                sha(destination) == expected[name]["sha256"],
                f"Export copy differs: {name}",
            )
        require(
            inventory(temporary) == expected,
            "Export inventory differs from verified sources",
        )
        atomic(
            temporary / "export_manifest.json",
            {
                "schema_version": 1,
                "complete": True,
                "run_fingerprint": verification["fingerprint"],
                "files": expected,
            },
        )
        os.replace(temporary, target)
        return {"complete": True, "export_dir": str(target), "files": len(expected)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "preflight",
            "stage",
            "run",
            "status",
            "verify",
            "export",
            "verify-export",
        ),
    )
    parser.add_argument("--config")
    parser.add_argument("--destination")
    args = parser.parse_args(argv)
    try:
        if args.action == "verify-export":
            root = absolute(args.destination, "destination")
            doc = read(root / "export_manifest.json")
            actual = inventory(root)
            actual.pop("export_manifest.json")
            require(
                doc.get("complete") is True and doc["files"] == actual,
                "Export hashes/identities differ",
            )
            print(json.dumps({"complete": True, "files": len(actual)}))
            return 0
        require(args.config, "--config is required")
        cfg = config(args.config)
        pipeline = Pipeline(cfg)
        if args.action == "status":
            result = read(pipeline.manifest_path)
            keys = (
                "state",
                "started_at",
                "completed_at",
                "failed_at",
                "error",
                "verified_images",
                "verified_crops",
                "prediction_rows",
            )
            stages = {
                stage: {key: record[key] for key in keys if key in record}
                for stage, record in result["stages"].items()
            }
            print(
                json.dumps(
                    {
                        "state": result["state"],
                        "stages": stages,
                        "logs": str(pipeline.root / "logs"),
                    },
                    indent=2,
                )
            )
            return 0
        with pipeline.lock():
            if args.action == "preflight":
                _, files = pipeline.preflight()
                result = {
                    "complete": True,
                    "images": len(files),
                    "effective_config": cfg,
                }
            elif args.action == "stage":
                result = pipeline.stage()
            elif args.action == "run":

                def interrupted(signum, frame):
                    raise KeyboardInterrupt(f"Interrupted by signal {signum}")

                signal.signal(signal.SIGTERM, interrupted)
                result = pipeline.run()
            elif args.action == "verify":
                result = pipeline.verify()
            else:
                require(args.destination, "--destination is required")
                result = pipeline.export(args.destination)
        print(json.dumps(result, indent=2))
        return 0
    except (
        Invalid,
        OSError,
        KeyError,
        ValueError,
        subprocess.SubprocessError,
        KeyboardInterrupt,
    ) as error:
        print(f"Pipeline failed: {error}", file=sys.stderr)
        return 1
