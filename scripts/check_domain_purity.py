"""AST purity guard: ensures backend/app/domain/ has ZERO external framework imports.

Only Python standard library modules and internal app.domain imports are allowed.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DOMAIN_DIR = ROOT_DIR / "backend" / "app" / "domain"

STDLIB_MODULES: frozenset[str] = frozenset(sys.stdlib_module_names) | {"__future__"}


def check_file(path: Path) -> list[str]:
    violations: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_pkg = alias.name.split(".")[0]
                if top_pkg not in STDLIB_MODULES and not alias.name.startswith("app.domain"):
                    violations.append(
                        f"{path.relative_to(ROOT_DIR)}:{node.lineno}: forbidden import '{alias.name}'"
                    )
        elif isinstance(node, ast.ImportFrom):
            # Relative imports inside domain/ are allowed only if they stay within domain/
            if node.level > 0:
                rel_parts = path.relative_to(DOMAIN_DIR).parts
                max_safe_level = len(rel_parts)
                if node.level > max_safe_level:
                    violations.append(
                        f"{path.relative_to(ROOT_DIR)}:{node.lineno}: "
                        f"relative import escapes app.domain (level={node.level})"
                    )
                continue

            module_name = node.module or ""
            top_pkg = module_name.split(".")[0]
            if top_pkg not in STDLIB_MODULES and not module_name.startswith("app.domain"):
                violations.append(
                    f"{path.relative_to(ROOT_DIR)}:{node.lineno}: "
                    f"forbidden import from '{module_name}'"
                )

    return violations


def main() -> int:
    if not DOMAIN_DIR.exists():
        print(f"ERROR: Domain directory not found: {DOMAIN_DIR}", file=sys.stderr)
        return 1

    py_files = sorted(DOMAIN_DIR.rglob("*.py"))
    all_violations: list[str] = []
    for py_file in py_files:
        all_violations.extend(check_file(py_file))

    if all_violations:
        print("DOMAIN PURITY CHECK FAILED (external imports detected):", file=sys.stderr)
        for violation in all_violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1

    print(f"Domain purity check PASSED ({len(py_files)} files scanned, 0 violations).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
