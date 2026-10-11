"""Build the frozen Maryland snapshot offline from hash-pinned official files."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import sqlite3
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import duckdb
import shapefile
from pyproj import CRS, Transformer

from pipeline.constants import FIPS, INDUSTRIES, OWNERSHIP, ROOT, YEAR
from pipeline.validate import validate_snapshot


class SourceIntegrityError(ValueError):
    """A pinned source file no longer matches its manifest."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def load_manifest(raw_dir: Path) -> dict[str, Any]:
    manifest = json.loads((raw_dir / "manifest.json").read_text())
    if manifest["manifest_version"] != 1 or manifest["year"] != YEAR or manifest["ownership"] != OWNERSHIP:
        raise SourceIntegrityError("Unsupported snapshot manifest")
    if len(manifest["sources"]) != 26 or {entry.get("fips") for entry in manifest["sources"] if entry["kind"] == "qcew"} != set(FIPS):
        raise SourceIntegrityError("The manifest must pin all 24 county CSVs, industry titles and Census boundaries")
    for entry in manifest["sources"]:
        if Path(entry["file"]).name != entry["file"]:
            raise SourceIntegrityError("Manifest filenames must be local source filenames")
        stored = (raw_dir / entry["file"]).read_bytes()
        if hashlib.sha256(stored).hexdigest() != entry["stored_sha256"]:
            raise SourceIntegrityError(f"Stored source hash mismatch: {entry['file']}")
        payload = gzip.decompress(stored) if entry["file"].endswith(".gz") else stored
        if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise SourceIntegrityError(f"Official source hash mismatch: {entry['file']}")
    return manifest


def transform_coordinates(coordinates: Any, transformer: Transformer) -> Any:
    if isinstance(coordinates[0], (float, int)):
        longitude, latitude = transformer.transform(coordinates[0], coordinates[1])
        return [round(longitude, 6), round(latitude, 6)]
    return [transform_coordinates(part, transformer) for part in coordinates]


def build_geometry(raw_dir: Path, manifest: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    entry = next(entry for entry in manifest["sources"] if entry["kind"] == "geometry")
    with zipfile.ZipFile(raw_dir / entry["file"]) as archive:
        prefix = "cb_2024_us_county_5m"
        source_crs = CRS.from_wkt(archive.read(f"{prefix}.prj").decode())
        transformer = Transformer.from_crs(source_crs, CRS.from_epsg(4326), always_xy=True)
        reader = shapefile.Reader(shp=io.BytesIO(archive.read(f"{prefix}.shp")), shx=io.BytesIO(archive.read(f"{prefix}.shx")), dbf=io.BytesIO(archive.read(f"{prefix}.dbf")), encoding="utf-8")
        features: list[dict[str, Any]] = []
        for shape_record in reader.iterShapeRecords():
            record = shape_record.record.as_dict()
            if record["STATEFP"] != "24":
                continue
            fips = record["GEOID"]
            original = shape_record.shape.__geo_interface__
            geometry = {"type": original["type"], "coordinates": transform_coordinates(original["coordinates"], transformer)}
            features.append({"type": "Feature", "id": fips, "properties": {"fips": fips, "name": record["NAMELSAD"], "GEOID": fips, "NAME": record["NAMELSAD"]}, "geometry": geometry})
    features.sort(key=lambda feature: feature["id"])
    counties = [{"fips": feature["id"], "name": feature["properties"]["name"]} for feature in features]
    return counties, {"type": "FeatureCollection", "features": features}


def build_observations(raw_dir: Path, manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]], duckdb.DuckDBPyConnection]:
    entries = [entry for entry in manifest["sources"] if entry["kind"] == "qcew"]
    with gzip.open(raw_dir / "industry_titles.csv.gz", "rt", encoding="utf-8-sig", newline="") as handle:
        titles = {row["industry_code"]: row["industry_title"].removeprefix(row["industry_code"] + " ") for row in csv.DictReader(handle)}
    missing_titles = set(INDUSTRIES) - titles.keys()
    if missing_titles:
        raise SourceIntegrityError(f"Official industry titles absent: {sorted(missing_titles)}")
    raw_rows: dict[tuple[str, str], dict[str, str]] = {}
    for entry in entries:
        with gzip.open(raw_dir / entry["file"], "rt", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if len(rows) != entry["rows"] or any(row["area_fips"] != entry["fips"] or row["year"] != YEAR or row["qtr"] != "A" for row in rows):
            raise SourceIntegrityError(f"Source county/year/period mismatch: {entry['file']}")
        for row in rows:
            if row["own_code"] == OWNERSHIP and row["size_code"] == "0" and ((row["industry_code"] == "10" and row["agglvl_code"] == "71") or row["agglvl_code"] == "74"):
                key = (row["area_fips"], row["industry_code"])
                if key in raw_rows:
                    raise SourceIntegrityError(f"Duplicate source observation {key}")
                if row["industry_code"] not in INDUSTRIES or row["disclosure_code"].strip() not in {"", "N"}:
                    raise SourceIntegrityError(f"Unexpected industry/disclosure code {key}")
                raw_rows[key] = row
    database = duckdb.connect(":memory:")
    paths = [str(raw_dir / entry["file"]) for entry in entries]
    database.execute("CREATE TABLE raw AS SELECT * FROM read_csv(?, all_varchar=true, union_by_name=true, header=true, nullstr='__CIVICATLAS_NEVER_NULL__')", [paths])
    database.execute("""CREATE TABLE selected AS SELECT area_fips AS fips, industry_code,
        CASE WHEN trim(disclosure_code) = 'N' THEN NULL ELSE CAST(annual_avg_emplvl AS DOUBLE) END AS employment,
        CASE WHEN trim(disclosure_code) = 'N' THEN NULL ELSE CAST(annual_avg_estabs AS DOUBLE) END AS establishments,
        CASE WHEN trim(disclosure_code) = 'N' THEN NULL ELSE CAST(avg_annual_pay AS DOUBLE) END AS annual_pay,
        CASE WHEN trim(disclosure_code) = 'N' THEN 'suppressed' ELSE 'available' END AS status
        FROM raw WHERE own_code = '5' AND size_code = '0' AND year = '2024' AND qtr = 'A'
        AND ((industry_code = '10' AND agglvl_code = '71') OR agglvl_code = '74')""")
    cursor = database.execute("SELECT * FROM selected ORDER BY fips, industry_code")
    columns = [column[0] for column in cursor.description]
    normalized = {(row[0], row[1]): dict(zip(columns, row, strict=True)) for row in cursor.fetchall()}
    by_fips = {entry["fips"]: entry for entry in entries}
    observations: list[dict[str, Any]] = []
    sources: list[dict[str, str]] = []
    for fips in FIPS:
        for code in INDUSTRIES:
            source_id = f"qcew-{YEAR}-{fips}-{code}"
            key = (fips, code)
            observation = normalized.get(key, {"fips": fips, "industry_code": code, "employment": None, "establishments": None, "annual_pay": None, "status": "missing"})
            observation = {**observation, "industry_name": titles[code], "source_id": source_id}
            observations.append(observation)
            entry = by_fips[fips]
            raw = raw_rows.get(key, {"observation_found": False, "area_fips": fips, "industry_code": code, "own_code": OWNERSHIP, "year": YEAR, "qtr": "A", "reason": "No matching county/private-sector/annual/aggregation row in the pinned CSV"})
            sources.append({"id": source_id, "url": entry["url"], "retrieved_at": entry["retrieved_at"], "sha256": entry["sha256"], "raw_json": canonical_json(raw)})
    return observations, sources, database


def sql_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def write_outputs(output_dir: Path, counties: list[dict[str, str]], observations: list[dict[str, Any]], geometry: dict[str, Any], sources: list[dict[str, str]], manifest: dict[str, Any], database: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    revision = "qcew-2024-md-private-" + hashlib.sha256(canonical_json(manifest).encode()).hexdigest()[:16]
    statuses = dict(sorted(Counter(row["status"] for row in observations).items()))
    metadata = {"dataset_revision": revision, "year": YEAR, "ownership": OWNERSHIP, "source_count": str(len(manifest["sources"])), "jurisdiction_count": "24", "observation_count": str(len(observations)), "retrieved_at": manifest["retrieved_at"], "geometry_year": "2024", "geometry_scale": "1:5,000,000"}
    schema = (ROOT / "migrations/0001_initial.sql").read_text()
    statements = [schema.rstrip(), "-- This artifact is rebuilt offline from data/raw/manifest.json."]
    table_rows: list[tuple[str, list[str], list[dict[str, Any]]]] = [
        ("counties", ["fips", "name"], counties),
        ("sources", ["id", "url", "retrieved_at", "sha256", "raw_json"], sources),
        ("observations", ["fips", "industry_code", "industry_name", "employment", "establishments", "annual_pay", "status", "source_id"], observations),
        ("metadata", ["key", "value"], [{"key": key, "value": value} for key, value in sorted(metadata.items())]),
    ]
    for table, columns, rows in table_rows:
        names = ",".join(columns)
        updates = ",".join(f"{column}=excluded.{column}" for column in columns if column not in ({"fips", "industry_code"} if table == "observations" else {columns[0]}))
        conflict = "fips,industry_code" if table == "observations" else columns[0]
        for row in rows:
            values = ",".join(sql_value(row[column]) for column in columns)
            statements.append(f"INSERT INTO {table} ({names}) VALUES ({values}) ON CONFLICT({conflict}) DO UPDATE SET {updates};")
    seed = "\n".join(statements) + "\n"
    (output_dir / "seed.sql").write_text(seed)
    with sqlite3.connect(":memory:") as sqlite:
        sqlite.executescript(seed)
        sqlite.executescript(seed)
        if sqlite.execute("PRAGMA foreign_key_check").fetchall() or sqlite.execute("SELECT COUNT(*) FROM observations").fetchone()[0] != len(observations):
            raise ValueError("D1-compatible SQL round trip failed")
    (output_dir / "maryland-counties.geojson").write_text(canonical_json(geometry) + "\n")
    database.execute("CREATE TABLE observations (fips VARCHAR, industry_code VARCHAR, industry_name VARCHAR, employment DOUBLE, establishments DOUBLE, annual_pay DOUBLE, status VARCHAR, source_id VARCHAR)")
    database.executemany("INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [[row[column] for column in table_rows[2][1]] for row in observations])
    database.execute("COPY (SELECT * FROM observations ORDER BY fips, industry_code) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(output_dir / "observations.parquet")])
    if database.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(output_dir / "observations.parquet")]).fetchone()[0] != len(observations):
        raise ValueError("Parquet round trip failed")
    artifacts = {name: {"sha256": hashlib.sha256((output_dir / name).read_bytes()).hexdigest(), "bytes": (output_dir / name).stat().st_size} for name in ("seed.sql", "maryland-counties.geojson", "observations.parquet")}
    summary: dict[str, Any] = {**metadata, "status_counts": statuses, "artifacts": artifacts, "metric_definitions": {"employment": "Annual average of monthly covered workplace employment levels; not resident workers", "establishments": "Annual average of quarterly establishment counts", "annual_pay": "Average annual pay based on employment and total annual wages; USD, not a median", "sector_share": "Sector annual average employment divided by the same county/private-ownership total; unavailable when numerator or denominator is unavailable"}}
    (output_dir / "snapshot.json").write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    return summary


def build(raw_dir: Path, output_dir: Path) -> dict[str, Any]:
    manifest = load_manifest(raw_dir)
    counties, geometry = build_geometry(raw_dir, manifest)
    observations, sources, database = build_observations(raw_dir, manifest)
    try:
        validate_snapshot(counties, observations, geometry)
        return write_outputs(output_dir, counties, observations, geometry, sources, manifest, database)
    finally:
        database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Rebuild in temporary storage and compare every committed output byte")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/processed")
    args = parser.parse_args()
    if args.check:
        with tempfile.TemporaryDirectory(prefix="civicatlas-check-") as temporary:
            result = build(ROOT / "data/raw", Path(temporary))
            for filename in ("seed.sql", "maryland-counties.geojson", "observations.parquet", "snapshot.json"):
                if (Path(temporary) / filename).read_bytes() != (args.output_dir / filename).read_bytes():
                    raise ValueError(f"Non-reproducible or stale output: {filename}")
        print(f"PASS: verified 26 source hashes, 24 jurisdictions, {result['observation_count']} observations and byte-identical outputs.")
    else:
        result = build(ROOT / "data/raw", args.output_dir)
        print(f"Built {result['dataset_revision']}: 24 jurisdictions, {result['observation_count']} observations, disclosure statuses {result['status_counts']}.")


if __name__ == "__main__":
    main()
