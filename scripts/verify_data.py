from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the frozen competition catalog")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--expected-rows", type=int, default=50_000)
    parser.add_argument("--sha256", dest="expected_sha256")
    args = parser.parse_args()

    path = Path(args.catalog)
    if not path.exists():
        raise SystemExit(f"Catalog not found: {path}")

    row_count = 0
    identifiers: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            parent_asin = str(payload.get("parent_asin") or "").strip()
            if not parent_asin:
                raise SystemExit(f"Row {line_number} has no parent_asin")
            if parent_asin in identifiers:
                raise SystemExit(f"Duplicate parent_asin at row {line_number}: {parent_asin}")
            identifiers.add(parent_asin)
            row_count += 1

    if row_count != args.expected_rows:
        raise SystemExit(f"Expected {args.expected_rows} rows, found {row_count}")

    actual_sha256 = sha256(path)
    if args.expected_sha256 and actual_sha256.lower() != args.expected_sha256.lower():
        raise SystemExit(f"SHA256 mismatch: {actual_sha256}")

    print(json.dumps({"catalog": str(path), "rows": row_count, "sha256": actual_sha256}, indent=2))


if __name__ == "__main__":
    main()
