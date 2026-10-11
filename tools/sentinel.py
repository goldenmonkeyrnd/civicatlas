#!/usr/bin/env python3
"""Bounded local-function reference and static-reachability checks.

This does not execute code, prove runtime behavior, or analyze third-party libraries.
A PASS applies only to the supported local-function graph described in the report.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Finding:
    kind: str
    file: str
    line: int
    message: str


@dataclass
class Module:
    name: str
    path: Path
    tree: ast.Module
    exports: set[str] = field(default_factory=set)
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = field(default_factory=dict)
    imports: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    classes: set[str] = field(default_factory=set)
    assignments: set[str] = field(default_factory=set)


def assigned_names(node: ast.AST) -> set[str]:
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)}


def import_module_name(module: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    package = module.split(".")[:-1]
    if node.level > len(package):
        return ""
    package = package[:len(package) - node.level + 1]
    return ".".join(package + ([node.module] if node.module else []))


def discover(root: Path, entry: str) -> dict[str, Module]:
    entry_module = entry.split(":", 1)[0]
    top_package = entry_module.split(".", 1)[0]
    package_dir = root / top_package
    paths = sorted(package_dir.rglob("*.py")) if package_dir.is_dir() else sorted(root.glob("*.py"))
    modules: dict[str, Module] = {}
    for path in paths:
        name = ".".join(path.relative_to(root).with_suffix("").parts)
        if name.endswith(".__init__"):
            name = name.removesuffix(".__init__")
        tree = ast.parse(path.read_text(), filename=str(path.relative_to(root)))
        module = Module(name=name, path=path, tree=tree)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                module.functions[node.name] = node
                module.exports.add(node.name)
            elif isinstance(node, ast.ClassDef):
                module.exports.add(node.name)
                module.classes.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                module.exports.update(assigned_names(node))
                module.assignments.update(assigned_names(node))
            elif isinstance(node, ast.ImportFrom):
                imported_module = import_module_name(name, node)
                for alias in node.names:
                    module.imports[alias.asname or alias.name] = (imported_module, alias.name)
                    module.exports.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    module.imports[alias.asname or alias.name.split(".")[0]] = (alias.name, None)
                    module.exports.add(alias.asname or alias.name.split(".")[0])
        modules[name] = module
    return modules


def qualified_attribute(node: ast.AST) -> list[str] | None:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        parent = qualified_attribute(node.value)
        return parent + [node.attr] if parent else None
    return None


def function_nodes(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    # A definition inside a function is not an invocation of its body.
    nodes: list[ast.AST] = []
    pending: list[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        nodes.append(node)
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            pending.extend(ast.iter_child_nodes(node))
    return nodes


def check(root: Path, entry: str, required: str) -> dict[str, Any]:
    root = root.resolve()
    modules = discover(root, entry)
    findings: list[Finding] = []
    unresolved: list[Finding] = []
    graph: dict[str, set[str]] = {}
    builtin_names = set(dir(builtins))
    for module in modules.values():
        relative = module.path.relative_to(root).as_posix()
        for node in module.tree.body:
            if isinstance(node, ast.ImportFrom):
                imported_module = import_module_name(module.name, node)
                if imported_module not in modules:
                    if imported_module.split(".", 1)[0] in modules:
                        findings.append(Finding("missing-module", relative, node.lineno, f"Local module {imported_module} is absent from the inspected package"))
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        unresolved.append(Finding("wildcard-import", relative, node.lineno, "Wildcard local imports are outside the supported graph"))
                    elif alias.name not in modules[imported_module].exports:
                        findings.append(Finding("missing-import", relative, node.lineno, f"{imported_module}.{alias.name} does not exist in the inspected module"))
        for name, function in module.functions.items():
            caller = f"{module.name}:{name}"
            graph[caller] = set()
            nodes = function_nodes(function)
            if function.decorator_list:
                unresolved.append(Finding("decorated-function", relative, function.lineno, "Decorators can replace function bindings and are outside the supported direct-call graph"))
            locals_ = {argument.arg for argument in (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs)}
            if function.args.vararg:
                locals_.add(function.args.vararg.arg)
            if function.args.kwarg:
                locals_.add(function.args.kwarg.arg)
            locals_.update(assigned_names(function))
            for node in nodes:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    unresolved.append(Finding("function-local-import", relative, node.lineno, "Imports inside functions are outside the supported module-level import graph"))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    unresolved.append(Finding("nested-function", relative, node.lineno, "Nested function definitions are outside the supported local-function graph"))
                if not isinstance(node, ast.Call):
                    continue
                parts = qualified_attribute(node.func)
                if parts and (parts[-1] in {"eval", "exec", "__import__", "getattr", "setattr", "import_module"}):
                    unresolved.append(Finding("dynamic-call", relative, node.lineno, "Dynamic lookup, code execution or import cannot be resolved statically"))
                    continue
                target: str | None = None
                if isinstance(node.func, ast.Name):
                    called = node.func.id
                    if called in module.functions:
                        target = f"{module.name}:{called}"
                    elif called in module.imports:
                        imported_module, imported_symbol = module.imports[called]
                        if imported_module in modules and imported_symbol in modules[imported_module].functions:
                            target = f"{imported_module}:{imported_symbol}"
                        elif imported_module in modules and imported_symbol not in modules[imported_module].classes:
                            unresolved.append(Finding("local-reexport-or-alias", relative, node.lineno, f"{imported_module}.{imported_symbol} is not a directly declared function or class"))
                    elif called in locals_:
                        unresolved.append(Finding("indirect-call", relative, node.lineno, f"Call through local variable {called} is outside the supported graph"))
                    elif called in module.assignments:
                        unresolved.append(Finding("indirect-call", relative, node.lineno, f"Call through module variable {called} is outside the supported graph"))
                    elif called not in builtin_names and called not in module.exports:
                        findings.append(Finding("missing-function", relative, node.lineno, f"Function name {called} has no statically declared binding"))
                elif parts and parts[0] in module.imports:
                    imported_module, imported_symbol = module.imports[parts[0]]
                    if imported_symbol is None and imported_module in modules:
                        # import checks as c; c.validate() is supported.
                        if len(parts) == 2:
                            symbol = parts[1]
                        elif parts == imported_module.split(".") + [parts[-1]]:
                            symbol = parts[-1]
                        else:
                            unresolved.append(Finding("local-attribute-chain", relative, node.lineno, "Nested local attribute chains are outside the supported graph"))
                            continue
                        if symbol not in modules[imported_module].exports:
                            findings.append(Finding("missing-function", relative, node.lineno, f"{imported_module}.{symbol} has no statically declared binding"))
                        elif symbol in modules[imported_module].functions:
                            target = f"{imported_module}:{symbol}"
                        elif symbol not in modules[imported_module].classes:
                            unresolved.append(Finding("local-reexport-or-alias", relative, node.lineno, f"{imported_module}.{symbol} is not a directly declared function or class"))
                elif not parts and not isinstance(node.func, ast.Attribute):
                    unresolved.append(Finding("computed-call", relative, node.lineno, "Computed call targets are outside the supported graph"))
                if target:
                    graph[caller].add(target)
    for target in (entry, required):
        if target not in graph:
            findings.append(Finding("missing-entry", "<configuration>", 0, f"Declared function {target} is absent from the inspected graph"))
    reachable: set[str] = set()
    pending = [entry] if entry in graph else []
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        pending.extend(graph.get(current, set()) - reachable)
    if required in graph and required not in reachable:
        if unresolved:
            unresolved.append(Finding("required-path-unresolved", "<configuration>", 0, f"Unsupported constructs prevent establishing a static call path from {entry} to {required}"))
        else:
            findings.append(Finding("disconnected-validator", "<configuration>", 0, f"No supported static call path from {entry} to {required}"))
    # Unsupported shapes anywhere in this declared source scope never produce PASS.
    findings.sort(key=lambda finding: (finding.file, finding.line, finding.kind))
    unresolved.sort(key=lambda finding: (finding.file, finding.line, finding.kind))
    status = "FAIL" if findings else "UNRESOLVED" if unresolved else "PASS"
    return {"checker_version": "1.0.0", "status": status, "entry": entry, "required": required,
        "scope": sorted(modules), "findings": [asdict(finding) for finding in findings], "unresolved": [asdict(finding) for finding in unresolved],
        "reachable": sorted(reachable), "edges": {caller: sorted(callees) for caller, callees in sorted(graph.items())},
        "limits": "Local function imports and direct call reachability only. External libraries and object methods are not analyzed. Static reachability does not prove branch execution, successful validation, or runtime correctness."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--entry", default="pipeline.build:main")
    parser.add_argument("--required", default="pipeline.validate:validate_snapshot")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = check(args.root, args.entry, args.required)
    except (OSError, SyntaxError, ValueError) as error:
        result = {"status": "ERROR", "error": str(error)}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{result['status']}: local function references and required-validator reachability")
        for finding in result.get("findings", []) + result.get("unresolved", []):
            print(f"  {finding['file']}:{finding['line']} [{finding['kind']}] {finding['message']}")
        if result["status"] == "PASS":
            print(f"  {args.entry} → {args.required}")
        if result["status"] == "ERROR":
            print(f"  {result['error']}")
        print("  Scope: direct local functions; no runtime or third-party correctness claim.")
    return {"PASS": 0, "FAIL": 1, "UNRESOLVED": 2, "ERROR": 3}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
