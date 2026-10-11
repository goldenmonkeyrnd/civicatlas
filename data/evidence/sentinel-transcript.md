# Sentinel: recorded terminal demonstration

Actual bash commands captured through a PTY, with deliberate pauses for reading. Video is rendered from this recording.

Dataset revision: `qcew-2024-md-private-cef931031ae7f224`.

`$DEMO` refers to a temporary copy of the public test fixtures. The demonstration repairs those copies; it does not modify the production pipeline.

## I use AI. I check its work.

Static reachability is evidence about code structure, not runtime correctness.

```text
CivicAtlas / bounded static-analysis demonstration

Actual Python commands, captured in a terminal.
Two checks: local function references and required-validator reachability.

The examples below are deliberate failures in temporary fixture copies.
The production pipeline stays unchanged.
```

## 1 / A malformed function reference fails

validateRecords was imported; the module actually defines validate_records.

```text
$ python tools/sentinel.py --root "$DEMO" --entry app:main --required checks:validate_records
FAIL: local function references and required-validator reachability
  app.py:1 [missing-import] checks.validateRecords does not exist in the inspected module
  <configuration>:0 [required-path-unresolved] Unsupported constructs prevent establishing a static call path from app:main to checks:validate_records
  app.py:4 [local-reexport-or-alias] checks.validateRecords is not a directly declared function or class
  Scope: direct local functions; no runtime or third-party correctness claim.

[exit 1]
```

## 2 / Fix the binding, then run the same check

The repaired local import and direct call now agree.

```text
--- app.py (before)
+++ app.py (after)
@@ -1,4 +1,4 @@
-from checks import validateRecords
+from checks import validate_records
 
 def main():
-    validateRecords()
+    validate_records()

$ python tools/sentinel.py --root "$DEMO" --entry app:main --required checks:validate_records
PASS: local function references and required-validator reachability
  app:main → checks:validate_records
  Scope: direct local functions; no runtime or third-party correctness claim.

[exit 0]
```

## 3 / A defined validator can still be disconnected

An import is not a production call path; test-only references do not count.

```text
$ python tools/sentinel.py --root "$DEMO" --entry app:main --required checks:validate
FAIL: local function references and required-validator reachability
  <configuration>:0 [disconnected-validator] No supported static call path from app:main to checks:validate
  Scope: direct local functions; no runtime or third-party correctness claim.

[exit 1]
```

## 4 / Connect the production path

This establishes a supported static call path. It does not prove every branch runs.

```text
$ python tools/sentinel.py --root "$DEMO" --entry app:main --required checks:validate
PASS: local function references and required-validator reachability
  app:main → checks:validate
  Scope: direct local functions; no runtime or third-party correctness claim.

[exit 0]
```

## 5 / Dynamic lookup is unresolved

Unknown constructs never receive a reassuring PASS.

```text
$ python tools/sentinel.py --root "$DEMO" --entry app:main --required checks:validate
UNRESOLVED: local function references and required-validator reachability
  <configuration>:0 [required-path-unresolved] Unsupported constructs prevent establishing a static call path from app:main to checks:validate
  app.py:4 [computed-call] Computed call targets are outside the supported graph
  app.py:4 [dynamic-call] Dynamic lookup, code execution or import cannot be resolved statically
  Scope: direct local functions; no runtime or third-party correctness claim.

[exit 2]
```

## 6 / Check the real CivicAtlas pipeline

The offline rebuild verifies source hashes and reproduces the published artifacts.

```text
$ python tools/sentinel.py
PASS: local function references and required-validator reachability
  pipeline.build:main → pipeline.validate:validate_snapshot
  Scope: direct local functions; no runtime or third-party correctness claim.

[exit 0]
$ python -m pipeline.build --check
PASS: verified 26 source hashes, 24 jurisdictions, 504 observations and byte-identical outputs.

[exit 0]
```

## 7 / Run the acceptance controls

Suppression, missing values, tampering, geometry and deliberate AST failures are covered.

```text
$ python -m unittest discover -s tests/python -q
----------------------------------------------------------------------
Ran 16 tests in 0.551s

OK

[exit 0]
```

## Keep the claim as precise as the check.

Pauline identified the engineering problem and directed agent-assisted investigation.

```text
PASS means the supported local-function graph resolves
and reaches the declared required validator.

Third-party libraries and object methods are outside this checker.
Runtime execution and data invariants are verified separately.

Python + DuckDB → Parquet + D1 import artifacts
24 Maryland jurisdictions / official 2024 sources
```
