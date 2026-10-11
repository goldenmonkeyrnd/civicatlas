"""Deterministic invariants at the production data boundary."""
from __future__ import annotations

import math
from typing import Any

from pipeline.constants import FIPS, INDUSTRIES


class SnapshotValidationError(ValueError):
    """The data snapshot cannot safely be published."""


def validate_snapshot(counties: list[dict[str, Any]], observations: list[dict[str, Any]], geometry: dict[str, Any]) -> None:
    expected = set(FIPS)
    county_ids = [county["fips"] for county in counties]
    if len(county_ids) != 24 or len(set(county_ids)) != 24 or set(county_ids) != expected:
        raise SnapshotValidationError("Expected exactly the 24 Maryland jurisdiction FIPS identifiers")
    if {county["fips"]: county["name"] for county in counties}["24510"] != "Baltimore city":
        raise SnapshotValidationError("Baltimore city must remain distinct from Baltimore County")
    features = geometry.get("features", [])
    feature_ids = [feature["properties"]["fips"] for feature in features]
    if len(feature_ids) != 24 or set(feature_ids) != expected:
        raise SnapshotValidationError("County geometry/data join is not one-to-one")
    keys: set[tuple[str, str]] = set()
    totals: dict[str, float] = {}
    for row in observations:
        key = (row["fips"], row["industry_code"])
        if key in keys or key[0] not in expected or key[1] not in INDUSTRIES:
            raise SnapshotValidationError(f"Duplicate or unexpected observation {key}")
        keys.add(key)
        if row["status"] not in {"available", "suppressed", "missing"}:
            raise SnapshotValidationError(f"Unknown disclosure status {key}")
        values = [row[field] for field in ("employment", "establishments", "annual_pay")]
        if row["status"] != "available" and any(value is not None for value in values):
            raise SnapshotValidationError(f"Unavailable observation contains numeric values {key}")
        if row["status"] == "available" and any(value is None or not math.isfinite(value) or value < 0 for value in values):
            raise SnapshotValidationError(f"Available observation has an invalid numeric value {key}")
        if key[1] == "10":
            if row["status"] != "available" or row["employment"] <= 0:
                raise SnapshotValidationError(f"County private-sector denominator is unavailable {key[0]}")
            totals[key[0]] = row["employment"]
    if keys != {(fips, code) for fips in FIPS for code in INDUSTRIES}:
        raise SnapshotValidationError("Expected one explicit observation, including missing statuses, per county/industry")
    for row in observations:
        if row["industry_code"] != "10" and row["status"] == "available" and row["employment"] > totals[row["fips"]]:
            raise SnapshotValidationError(f"Sector employment exceeds its matching county private-sector total: {row['fips']}/{row['industry_code']}")
