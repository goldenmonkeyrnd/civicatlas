"""Released-tool adoption checks against the actual pipeline and snapshot."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class SentinelsIntegrationTests(unittest.TestCase):
    def inspect(self, root: Path = ROOT) -> tuple[int, dict]:
        completed = subprocess.run(
            [sys.executable, "-m", "sentinels", "check", "--root", str(root),
             "--config", "sentinels.toml", "--json"],
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(completed.stderr, "", completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["checker_version"], "0.2.0")
        return completed.returncode, report

    def stage(self, destination: Path) -> None:
        shutil.copytree(ROOT / "pipeline", destination / "pipeline", ignore=shutil.ignore_patterns("__pycache__"))
        (destination / "data/processed").mkdir(parents=True)
        shutil.copy(ROOT / "data/processed/snapshot.json", destination / "data/processed/snapshot.json")
        shutil.copy(ROOT / "sentinels.toml", destination / "sentinels.toml")

    def test_real_pipeline_contracts_pass_with_source_witnesses(self) -> None:
        code, report = self.inspect()
        self.assertEqual((code, report["status"]), (0, "PASS"), report)
        results = {result["id"]: result for result in report["contracts"]}
        self.assertEqual(set(results), {"production-validation", "pinned-manifest-handoff", "published-snapshot-shape", "pipeline-coverage"})
        self.assertTrue(results["production-validation"]["witness"])
        self.assertTrue(results["pinned-manifest-handoff"]["witness"])
        self.assertFalse(any(path.startswith("tests/") for path in report["scope"]))
        inventory = results["pipeline-coverage"]["coverage"]
        self.assertEqual(inventory["roots"], ["pipeline"])
        self.assertEqual(len(inventory["surfaces"]), 15)
        self.assertEqual(sum(item["classification"] == "contract" for item in inventory["surfaces"]), 4)
        self.assertEqual(sum(item["classification"] == "internal" for item in inventory["surfaces"]), 11)
        self.assertTrue(all(item["reason"] for item in inventory["surfaces"] if item["classification"] == "internal"))
        self.assertEqual(inventory["stale"], [])

    def test_removed_real_validator_connection_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="civicatlas-contract-") as temporary:
            root = Path(temporary)
            self.stage(root)
            source = root / "pipeline/build.py"
            text = source.read_text()
            call = "        validate_snapshot(counties, observations, geometry)\n"
            self.assertEqual(text.count(call), 1)
            source.write_text(text.replace(call, ""))
            code, report = self.inspect(root)
            self.assertEqual(code, 1, report)
            result = next(item for item in report["contracts"] if item["id"] == "production-validation")
            self.assertEqual(result["status"], "FAIL", result)

    def test_overwritten_manifest_does_not_satisfy_the_handoff(self) -> None:
        with tempfile.TemporaryDirectory(prefix="civicatlas-contract-") as temporary:
            root = Path(temporary)
            self.stage(root)
            source = root / "pipeline/build.py"
            text = source.read_text()
            call = "    observations, sources, database = build_observations(raw_dir, manifest)\n"
            self.assertEqual(text.count(call), 1)
            source.write_text(text.replace(call, "    manifest = {}\n" + call))
            code, report = self.inspect(root)
            self.assertEqual(code, 1, report)
            result = next(item for item in report["contracts"] if item["id"] == "pinned-manifest-handoff")
            self.assertEqual(result["status"], "FAIL", result)

    def test_snapshot_json_type_drift_fails_without_coercion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="civicatlas-contract-") as temporary:
            root = Path(temporary)
            self.stage(root)
            artifact = root / "data/processed/snapshot.json"
            snapshot = json.loads(artifact.read_text())
            snapshot["year"] = int(snapshot["year"])
            artifact.write_text(json.dumps(snapshot))
            code, report = self.inspect(root)
            self.assertEqual(code, 1, report)
            result = next(item for item in report["contracts"] if item["id"] == "published-snapshot-shape")
            self.assertEqual(result["status"], "FAIL", result)

    def test_new_pipeline_surface_requires_an_explicit_coverage_decision(self) -> None:
        with tempfile.TemporaryDirectory(prefix="civicatlas-contract-") as temporary:
            root = Path(temporary)
            self.stage(root)
            source = root / "pipeline/build.py"
            source.write_text(source.read_text() + "\n\ndef new_public_publish() -> dict[str, str]:\n    return {'unreviewed': 'surface'}\n")
            code, report = self.inspect(root)
            self.assertEqual(code, 1, report)
            results = {result["id"]: result for result in report["contracts"]}
            self.assertEqual(results["production-validation"]["status"], "PASS")
            coverage = results["pipeline-coverage"]
            self.assertEqual(coverage["status"], "FAIL", coverage)
            self.assertIn("unclassified-surface", {finding["code"] for finding in coverage["findings"]})
            added = next(item for item in coverage["coverage"]["surfaces"] if item["id"] == "pipeline.build:new_public_publish")
            self.assertEqual(added["classification"], "unclassified")

    def test_removed_pipeline_surface_leaves_a_stale_coverage_decision(self) -> None:
        with tempfile.TemporaryDirectory(prefix="civicatlas-contract-") as temporary:
            root = Path(temporary)
            self.stage(root)
            source = root / "pipeline/acquire.py"
            source.write_text(source.read_text().replace("def download(url: str)", "def _download(url: str)").replace("pool.map(download,", "pool.map(_download,"))
            code, report = self.inspect(root)
            self.assertEqual(code, 1, report)
            coverage = next(item for item in report["contracts"] if item["id"] == "pipeline-coverage")
            self.assertEqual(coverage["status"], "FAIL", coverage)
            self.assertIn("pipeline.acquire:download", coverage["coverage"]["stale"])
            self.assertIn("stale-coverage", {finding["code"] for finding in coverage["findings"]})


if __name__ == "__main__":
    unittest.main()
