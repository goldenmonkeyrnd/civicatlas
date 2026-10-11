"""Pin official public sources; routine builds never silently fetch a new revision."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from pipeline.constants import FIPS, GEOMETRY_URL, ROOT, TITLES_URL, YEAR


def download(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "CivicAtlas/0.1 (+https://goldenmonkey.dev; public-source research)"})
    with urlopen(request, timeout=90) as response:
        payload = response.read(16 * 1024 * 1024)
        if response.status != 200 or not payload or len(payload) >= 16 * 1024 * 1024:
            raise RuntimeError(f"Unexpected public-source response from {url}")
        if b"<html" in payload[:200].lower():
            raise RuntimeError(f"Source returned HTML rather than data: {url}")
        return payload


def acquire(raw_dir: Path, refresh: bool = False) -> dict[str, object]:
    manifest_path = raw_dir / "manifest.json"
    if manifest_path.exists() and not refresh:
        raise RuntimeError("A pinned snapshot already exists. Rebuild it offline; use --refresh to deliberately acquire a new revision.")
    requests = [(f"qcew-{YEAR}-{fips}.csv.gz", f"https://data.bls.gov/cew/data/api/{YEAR}/a/area/{fips}.csv", "qcew", fips) for fips in FIPS]
    requests.extend([("cb_2024_us_county_5m.zip", GEOMETRY_URL, "geometry", None), ("industry_titles.csv.gz", TITLES_URL, "industry_titles", None)])
    retrieved = datetime.now(timezone.utc).isoformat(timespec="seconds")
    raw_dir.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[Path, Path]] = []
    entries: list[dict[str, object]] = []
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            payloads = list(pool.map(download, [item[1] for item in requests]))
        for (filename, url, kind, fips), payload in zip(requests, payloads, strict=True):
            if kind == "qcew":
                rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
                if not rows or any(row.get("area_fips") != fips or row.get("year") != YEAR or row.get("qtr") != "A" for row in rows):
                    raise ValueError(f"Unexpected county/year/period in {url}")
            stored = gzip.compress(payload, compresslevel=9, mtime=0) if filename.endswith(".gz") else payload
            destination = raw_dir / filename
            temporary = raw_dir / f".{filename}.new"
            temporary.write_bytes(stored)
            staged.append((temporary, destination))
            entry: dict[str, object] = {"file": filename, "url": url, "kind": kind, "retrieved_at": retrieved,
                "sha256": hashlib.sha256(payload).hexdigest(), "stored_sha256": hashlib.sha256(stored).hexdigest(), "bytes": len(payload)}
            if fips:
                entry["fips"] = fips
                entry["rows"] = len(rows)
            entries.append(entry)
        manifest: dict[str, object] = {"manifest_version": 1, "year": YEAR, "ownership": "5", "retrieved_at": retrieved, "sources": entries}
        for temporary, destination in staged:
            os.replace(temporary, destination)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return manifest
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Explicitly replace the pinned official-source snapshot")
    args = parser.parse_args()
    manifest = acquire(ROOT / "data/raw", refresh=args.refresh)
    print(f"Pinned {len(manifest['sources'])} official source files for 24 Maryland jurisdictions.")


if __name__ == "__main__":
    main()
