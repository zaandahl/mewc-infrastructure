"""Non-overwriting key generation and exact cloud keypair registration."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .cli import Error, require


def run(args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=90)
    require(result.returncode == 0, f"{args[0]} failed (exit {result.returncode})")
    return result.stdout


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        require(len(args) == 2, "Expected KEYPAIR_NAME /absolute/private-key-path")
        name, keypath = args
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name), "Invalid keypair name")
        path = Path(keypath)
        require(path.is_absolute(), "Private key path must be absolute")
        os.umask(0o077)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        public_path = Path(str(path) + ".pub")
        if path.exists() or public_path.exists():
            require(path.is_file() and public_path.is_file(), "Incomplete keypair exists; refusing overwrite")
            require(path.stat().st_mode & 0o077 == 0, "Private key permissions must be 0600")
            derived = run(["ssh-keygen", "-y", "-f", str(path)]).split()[:2]
            require(derived == public_path.read_text().split()[:2], "Existing public/private keys do not match")
        else:
            run(["ssh-keygen", "-t", "ed25519", "-f", str(path), "-N", ""])
        pairs = json.loads(run(["openstack", "keypair", "list", "-f", "json"]))
        if any(pair.get("Name") == name for pair in pairs):
            public = run(["openstack", "keypair", "show", "--public-key", name])
            require(public.split()[:2] == public_path.read_text().split()[:2], "Cloud keypair name exists with another key; refusing replacement")
        else:
            run(["openstack", "keypair", "create", "--public-key", str(public_path), name, "-f", "json"])
        print(f"Keypair {name} matches {public_path}; no existing key was overwritten.")
    except (Error, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
