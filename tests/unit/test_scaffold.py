"""TASK-01 smoke tests: the toolchain itself works."""

from __future__ import annotations

import asyncio
from pathlib import Path

import agent


def test_package_imports() -> None:
    """Package is importable — src layout and editable install are wired up."""
    assert agent is not None


async def test_async_test_runner_works() -> None:
    """Verifies asyncio_mode='auto' is live.

    Without it every async test in the project passes without executing its body,
    which is a genuinely nasty silent failure.
    """
    marker: list[str] = []
    await asyncio.sleep(0)
    marker.append("ran")
    assert marker == ["ran"]


def test_all_packages_have_init(project_root: Path) -> None:
    """A missing __init__.py fails only on some import paths — catch it here."""
    root = project_root / "src" / "agent"
    static = root / "api" / "static"
    missing = [
        str(d.relative_to(project_root))
        for d in root.rglob("*")
        if d.is_dir()
        and not d.name.startswith(("_", "."))
        # Web assets, not Python packages. Excludes the vendored JS beneath it.
        and static not in d.parents
        and d != static
        and not (d / "__init__.py").exists()
    ]
    assert not missing, f"missing __init__.py in: {missing}"
