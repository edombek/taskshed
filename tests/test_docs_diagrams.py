"""Embedded ```mermaid blocks in markdown must match docs/diagrams/*.mmd.

Run ``scripts/sync_diagram_blocks.py`` after editing a source; this test is
the CI tripwire that stops the copies from drifting apart.
"""

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "sync_diagram_blocks", _ROOT / "scripts" / "sync_diagram_blocks.py")
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)
BLOCK, DIAGRAMS, DOCS, render = sync.BLOCK, sync.DIAGRAMS, sync.DOCS, sync.render

DOC_NAMES = [doc.name for doc in DOCS if doc.exists()]


def test_diagram_sources_present():
    assert {p.stem for p in DIAGRAMS.glob("*.mmd")} == {
        "architecture", "er-diagram", "linking-flow", "reminder-flow"}


@pytest.mark.parametrize("doc", DOCS, ids=lambda d: str(d))
def test_embedded_blocks_match_sources(doc: Path):
    if not doc.exists():
        pytest.skip(f"{doc} missing")
    text = doc.read_text(encoding="utf-8")
    for m in BLOCK.finditer(text):
        assert m.group(0) == render(m.group(1)), (
            f"{doc.name}: mermaid block '{m.group(1)}' is stale — "
            "run scripts/sync_diagram_blocks.py")
