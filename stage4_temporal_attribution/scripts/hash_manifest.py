"""Generate or verify the Stage 4 inventory (relative path, bytes, SHA-256)."""

import argparse
from hashlib import file_digest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "SHA256SUMS.txt"
HEADER = "path\tbytes\tsha256"


def inventory(root):
    files = sorted(
        p for p in root.rglob("*")
        if p.is_file() and p != root / NAME and "__pycache__" not in p.parts
        and p.suffix not in {".pyc", ".pyo"}
    )
    lines = [HEADER]
    for path in files:
        with path.open("rb") as source:
            digest = file_digest(source, "sha256").hexdigest()
        lines.append(f"{path.relative_to(root).as_posix()}\t{path.stat().st_size}\t{digest}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Compare without writing")
    args = parser.parse_args()
    manifest = ROOT / NAME
    payload = inventory(ROOT)
    if args.check:
        if manifest.read_bytes() != payload:
            raise SystemExit("Stage 4 SHA-256 inventory differs")
    else:
        manifest.write_bytes(payload)
    print(f"{NAME}: {len(payload.splitlines()) - 1} files {'verified' if args.check else 'recorded'}")


if __name__ == "__main__":
    main()
