"""Enforce the data-manager-only database boundary."""

import ast
from pathlib import Path

import pytest

FORBIDDEN_MODULES = {
    "pymongo",
    "motor",
    "bson",
    "aiomysql",
    "pymysql",
    "PyMySQL",
    "sqlalchemy",
}
ROOT = Path(__file__).parents[1]


def violations(source: str, filename: str = "<string>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
            found.extend(
                name for name in names if name.split(".")[0] in FORBIDDEN_MODULES
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in FORBIDDEN_MODULES:
                found.append(node.module)
        elif isinstance(node, ast.Constant) and node.value == "mysql":
            found.append('database="mysql"')
    return found


def test_no_direct_database_clients_or_mysql_requests() -> None:
    for directory in ("tradeengine", "shared", "contracts", "scripts"):
        for path in (ROOT / directory).rglob("*.py"):
            assert violations(path.read_text(), str(path)) == []

    requirements = (ROOT / "requirements.txt").read_text().lower()
    for package in ("motor", "pymongo", "aiomysql", "pymysql", "sqlalchemy"):
        assert package not in requirements


def test_guard_detects_forbidden_import(tmp_path: Path) -> None:
    fixture = tmp_path / "forbidden.py"
    fixture.write_text("import pymongo\n")
    with pytest.raises(AssertionError):
        assert violations(fixture.read_text(), str(fixture)) == []


def test_database_scripts_are_removed() -> None:
    assert list((ROOT / "scripts").rglob("*.sql")) == []
    assert not (ROOT / "scripts" / "check-mongodb.py").exists()
    assert not (ROOT / "scripts" / "setup-mongodb.sh").exists()
