"""Structural guard against client exception paths masking unavailable data."""

import ast
from pathlib import Path

CLIENT_MODULES = (
    Path("tradeengine/services/data_manager_client.py"),
    Path("tradeengine/db/mongodb_client.py"),
    Path("tradeengine/exchange/binance.py"),
)


def _empty_collection_return(node: ast.Return) -> bool:
    value = node.value
    if isinstance(value, ast.List | ast.Set):
        return not value.elts
    if isinstance(value, ast.Dict):
        return not value.keys
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id
        in {
            "dict",
            "set",
            "list",
        }
    )


def test_client_exception_paths_never_return_empty_collections():
    violations: list[str] = []
    for path in CLIENT_MODULES:
        tree = ast.parse(path.read_text(), filename=str(path))
        for handler in (
            node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)
        ):
            for node in ast.walk(handler):
                if isinstance(node, ast.Return) and _empty_collection_return(node):
                    violations.append(f"{path}:{node.lineno}")

    assert not violations, (
        "Client exception paths must raise, not return empty collections: "
        + ", ".join(violations)
    )
