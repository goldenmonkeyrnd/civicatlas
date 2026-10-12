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
        self.assertEqual(report["checker_version"], "0.1.0")
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
        self.assertEqual(set(results), {"production-validation", "pinned-manifest-handoff", "published-snapshot-shape"})
        self.assertTrue(results["production-validation"]["witness"])
        self.assertTrue(results["pinned-manifest-handoff"]["witness"])
        self.assertFalse(any(path.startswith("tests/") for path in report["scope"]))

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


if __name__ == "__main__":
    unittest.main()
