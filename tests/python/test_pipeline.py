from __future__ import annotations

import copy
import gzip
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

import shapefile

from pipeline.build import SourceIntegrityError, build_geometry, load_manifest
from pipeline.constants import FIPS, ROOT
from pipeline.validate import SnapshotValidationError, validate_snapshot


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_manifest(ROOT / "data/raw")
        cls.counties, cls.geometry = build_geometry(ROOT / "data/raw", cls.manifest)
        cls.database = sqlite3.connect(":memory:")
        cls.database.row_factory = sqlite3.Row
        cls.database.executescript((ROOT / "data/processed/seed.sql").read_text())
        cls.observations = [dict(row) for row in cls.database.execute("SELECT * FROM observations")]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.close()

    def test_exact_county_join_and_baltimore_distinction(self) -> None:
        rows = {row["fips"]: row["name"] for row in self.database.execute("SELECT * FROM counties")}
        self.assertEqual(set(rows), set(FIPS))
        self.assertEqual(rows["24510"], "Baltimore city")
        self.assertEqual(rows["24005"], "Baltimore County")
        self.assertEqual(self.database.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_suppression_masks_encoded_zero_without_losing_provenance(self) -> None:
        row = self.database.execute("SELECT o.*, s.raw_json FROM observations o JOIN sources s ON s.id=o.source_id WHERE o.fips='24510' AND o.industry_code='11'").fetchone()
        self.assertEqual(row["status"], "suppressed")
        self.assertIsNone(row["employment"])
        self.assertIsNone(row["establishments"])
        self.assertIsNone(row["annual_pay"])
        raw = json.loads(row["raw_json"])
        self.assertEqual(raw["disclosure_code"], "N")
        self.assertEqual(raw["annual_avg_emplvl"], "0")

    def test_missing_rows_are_explicit_and_distinct_from_suppression(self) -> None:
        rows = self.database.execute("SELECT o.*, s.raw_json FROM observations o JOIN sources s ON s.id=o.source_id WHERE status='missing'").fetchall()
        self.assertGreater(len(rows), 0)
        for row in rows:
            self.assertIsNone(row["employment"])
            self.assertFalse(json.loads(row["raw_json"])["observation_found"])

    def test_known_official_total_and_sector_share_denominator(self) -> None:
        total = self.database.execute("SELECT employment FROM observations WHERE fips='24510' AND industry_code='10'").fetchone()[0]
        health = self.database.execute("SELECT employment FROM observations WHERE fips='24510' AND industry_code='62'").fetchone()[0]
        self.assertEqual(total, 270536)
        self.assertEqual(health, 74996)
        self.assertAlmostEqual(health / total, 0.277212661901195)

    def test_available_zero_is_retained_and_negative_value_is_rejected(self) -> None:
        rows = copy.deepcopy(self.observations)
        sector = next(row for row in rows if row["industry_code"] != "10" and row["status"] == "available")
        sector["employment"] = 0.0
        validate_snapshot(self.counties, rows, self.geometry)
        sector["employment"] = -1.0
        with self.assertRaises(SnapshotValidationError):
            validate_snapshot(self.counties, rows, self.geometry)

    def test_unavailable_numeric_values_are_rejected(self) -> None:
        rows = copy.deepcopy(self.observations)
        next(row for row in rows if row["status"] == "suppressed")["employment"] = 0.0
        with self.assertRaises(SnapshotValidationError):
            validate_snapshot(self.counties, rows, self.geometry)

    def test_shapefile_rings_and_multipart_geometry_are_preserved(self) -> None:
        with zipfile.ZipFile(ROOT / "data/raw/cb_2024_us_county_5m.zip") as archive:
            prefix = "cb_2024_us_county_5m"
            reader = shapefile.Reader(shp=io.BytesIO(archive.read(prefix+'.shp')), shx=io.BytesIO(archive.read(prefix+'.shx')), dbf=io.BytesIO(archive.read(prefix+'.dbf')))
            ring_counts = {record.record["GEOID"]: len(record.shape.parts) for record in reader.iterShapeRecords() if record.record["STATEFP"] == "24"}
        has_multipart = False
        for feature in self.geometry["features"]:
            geometry = feature["geometry"]
            polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
            has_multipart = has_multipart or len(polygons) > 1
            self.assertEqual(sum(len(polygon) for polygon in polygons), ring_counts[feature["id"]])
            for polygon in polygons:
                for ring in polygon:
                    self.assertEqual(ring[0], ring[-1])
                    for longitude, latitude in ring:
                        self.assertTrue(-80 < longitude < -74)
                        self.assertTrue(37 < latitude < 40)
        self.assertTrue(has_multipart)

    def test_tampered_source_is_rejected_before_transformation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            shutil.copytree(ROOT / "data/raw", target, dirs_exist_ok=True)
            source = target / "qcew-2024-24510.csv.gz"
            source.write_bytes(source.read_bytes() + b"tamper")
            with self.assertRaisesRegex(SourceIntegrityError, "Stored source hash mismatch"):
                load_manifest(target)
