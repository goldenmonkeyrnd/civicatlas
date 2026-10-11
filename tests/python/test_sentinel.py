from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.sentinel import check

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures/sentinel"


class SentinelTests(unittest.TestCase):
    def inspect(self, name: str, required: str = "checks:validate") -> dict:
        return check(FIXTURES / name, "app:main", required)

    def test_valid_direct_calls_reach_validator(self) -> None:
        result = self.inspect("valid")
        self.assertEqual(result["status"], "PASS")
        self.assertIn("checks:validate", result["reachable"])

    def test_malformed_local_import_fails(self) -> None:
        result = self.inspect("malformed", "checks:validate_records")
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("missing-import", [finding["kind"] for finding in result["findings"]])
        missing_import = next(finding for finding in result["findings"] if finding["kind"] == "missing-import")
        self.assertEqual(missing_import["file"], "app.py")
        self.assertEqual(missing_import["line"], 1)

    def test_disconnected_validator_fails(self) -> None:
        self.assertIn("disconnected-validator", [finding["kind"] for finding in self.inspect("disconnected")["findings"]])

    def test_a_test_reference_is_not_production_reachability(self) -> None:
        result = self.inspect("test_only")
        self.assertEqual(result["status"], "FAIL")
        self.assertNotIn("checks:validate", result["reachable"])
        self.assertIn("checks:validate", result["edges"]["test_app:test_validation"])

    def test_dynamic_lookup_never_passes(self) -> None:
        result = self.inspect("dynamic")
        self.assertEqual(result["status"], "UNRESOLVED")
        self.assertIn("dynamic-call", [finding["kind"] for finding in result["unresolved"]])

    def test_module_import_alias_is_supported(self) -> None:
        self.assertEqual(self.inspect("module_alias")["status"], "PASS")

    def test_real_pipeline_has_the_required_static_path(self) -> None:
        result = check(ROOT, "pipeline.build:main", "pipeline.validate:validate_snapshot")
        self.assertEqual(result["status"], "PASS", result)

    def test_decorated_and_indirect_bindings_cannot_pass(self) -> None:
        examples = [
            'from checks import validate\n\ndef decorator(fn):\n    return fn\n\n@decorator\ndef main():\n    validate()\n',
            'from checks import validate\n\nalias = validate\n\ndef main():\n    alias()\n',
            'def main():\n    from checks import validate\n    validate()\n',
        ]
        for source in examples:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "app.py").write_text(source)
                (root / "checks.py").write_text('def validate():\n    return True\n')
                result = check(root, "app:main", "checks:validate")
                self.assertNotEqual(result["status"], "PASS")
                self.assertGreater(len(result["unresolved"]), 0)
