"""Regression tests for repository architecture boundaries."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

PACKAGE_ROOT: Final = Path(__file__).parents[1] / "src" / "media_recommender"
FORBIDDEN_IMPORTS: Final = {
    "domain": (
        "fastapi",
        "sqlalchemy",
        "media_recommender.application",
        "media_recommender.integrations",
        "media_recommender.persistence",
    ),
    "application": (
        "fastapi",
        "sqlalchemy",
        "media_recommender.integrations",
        "media_recommender.persistence",
    ),
    "persistence": (
        "fastapi",
        "media_recommender.integrations",
    ),
}
WEB_FORBIDDEN_IMPORTS: Final = (
    "media_recommender.persistence.models",
    "media_recommender.integrations.jellyfin.models",
    "media_recommender.integrations.netflix.models",
    "media_recommender.integrations.tmdb.models",
)


def _module_imports(path: Path) -> set[str]:
    """Return absolute module names imported by one Python source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def test_phase_one_layers_do_not_import_forbidden_dependencies() -> None:
    """Keep domain, application, and persistence dependencies pointed inward."""
    violations: list[str] = []
    for layer, forbidden_prefixes in FORBIDDEN_IMPORTS.items():
        for path in sorted((PACKAGE_ROOT / layer).rglob("*.py")):
            violations.extend(
                f"{path.relative_to(PACKAGE_ROOT)} imports {imported_module}"
                for imported_module in sorted(_module_imports(path))
                if imported_module.startswith(forbidden_prefixes)
            )

    assert violations == []


def test_web_layer_does_not_import_orm_records_or_provider_transport_dtos() -> None:
    """Keep HTTP rendering and schemas independent from persistence and provider payloads."""
    violations = [
        f"{path.relative_to(PACKAGE_ROOT)} imports {imported_module}"
        for path in sorted((PACKAGE_ROOT / "web").rglob("*.py"))
        for imported_module in sorted(_module_imports(path))
        if imported_module.startswith(WEB_FORBIDDEN_IMPORTS)
    ]

    assert violations == []
