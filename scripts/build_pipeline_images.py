#!/usr/bin/env python3
"""Build compatible stages from exact Git sources; emit immutable local image IDs."""

import argparse
import hashlib
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path

ORDER = ("detect", "flow", "snip", "predict", "exif", "box")


def execute(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument(
        "--checkouts",
        type=Path,
        required=True,
        help="Parent containing mewc-detect, mewc-flow, etc.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    lock = json.loads(args.sources.read_text())
    if lock.get("schema_version") != 1 or set(lock.get("sources", {})) != set(ORDER):
        raise ValueError("Source lock must name all six stages")
    if args.output.exists():
        raise ValueError("Output image lock already exists; choose a new output path")
    built, evidence = {}, {}
    for role in ORDER:
        repo = args.checkouts / f"mewc-{role}"
        source = lock["sources"][role]
        commit = source["commit"]
        if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
            raise ValueError("Full Git commit required")
        actual = execute(
            ["git", "-C", str(repo), "rev-parse", commit + "^{commit}"],
            capture_output=True,
        ).stdout.strip()
        if actual != commit:
            raise ValueError("Source commit does not resolve exactly")
        # Build a clean archive of the named commit, independent of checkout edits.
        with tempfile.TemporaryDirectory(prefix=f"mewc-build-{role}-") as temporary:
            root = Path(temporary)
            archive = root / "source.tar"
            execute(
                [
                    "git",
                    "-C",
                    str(repo),
                    "archive",
                    "--format=tar",
                    "--output",
                    str(archive),
                    commit,
                ]
            )
            tree = root / "context"
            tree.mkdir()
            with tarfile.open(archive) as stream:
                stream.extractall(tree, filter="data")
            tag = f"mewc-local/{role}:{commit}"
            command = ["docker", "build", "--tag", tag]
            parent = (
                "flow"
                if role == "predict"
                else ("detect" if role in ("snip", "exif", "box") else None)
            )
            if parent:
                command += [
                    "--build-arg",
                    f"MEWC_{parent.upper()}_BASE={built[parent]['tag']}",
                ]
            execute(command + [str(tree)])
            detail = json.loads(
                execute(["docker", "image", "inspect", tag], capture_output=True).stdout
            )[0]
            if detail["Os"] != "linux" or detail["Architecture"] != "amd64":
                raise ValueError("Pipeline runtime must be Linux amd64")
            built[role] = {"tag": tag, "id": detail["Id"]}
            evidence[role] = {
                **source,
                "image_id": detail["Id"],
                "source_archive_sha256": hashlib.sha256(
                    archive.read_bytes()
                ).hexdigest(),
                "dockerfile": (tree / "Dockerfile").read_text(),
                "parent_image_id": built[parent]["id"] if parent else None,
            }
    result = {
        "schema_version": 1,
        "images": {
            role: value["id"] for role, value in built.items() if role != "flow"
        },
        "builds": evidence,
        "transport": "Local image IDs: preserve docker image save archive or publish and record OCI digests.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("Built all stage images; immutable lock:", args.output)


if __name__ == "__main__":
    main()
