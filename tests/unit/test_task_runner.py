"""The Makefile and its Windows shim must expose the same targets.

`make` is not installed on Windows by default, so make.cmd mirrors the Makefile.
Two files describing the same commands drift; this test is the guard.
"""

from __future__ import annotations

import re
from pathlib import Path


def _makefile_targets(root: Path) -> set[str]:
    text = (root / "Makefile").read_text(encoding="utf-8")
    phony = re.search(r"^\.PHONY:\s*(.+)$", text, re.MULTILINE)
    assert phony, "Makefile has no .PHONY line"
    return set(phony.group(1).split())


def _cmd_targets(root: Path) -> set[str]:
    text = (root / "make.cmd").read_text(encoding="utf-8")
    labels = set(re.findall(r"^:([a-z][a-z0-9-]*)$", text, re.MULTILINE))
    return labels - {"eof", "unknown"}


def test_shim_covers_every_makefile_target(project_root: Path) -> None:
    missing = _makefile_targets(project_root) - _cmd_targets(project_root)
    assert not missing, f"make.cmd is missing targets: {sorted(missing)}"


def test_shim_has_no_extra_targets(project_root: Path) -> None:
    extra = _cmd_targets(project_root) - _makefile_targets(project_root)
    assert not extra, f"make.cmd has targets the Makefile lacks: {sorted(extra)}"


def test_both_runners_exist(project_root: Path) -> None:
    assert (project_root / "Makefile").exists()
    assert (project_root / "make.cmd").exists()
