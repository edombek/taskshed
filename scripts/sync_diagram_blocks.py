#!/usr/bin/env python3
"""Keep embedded ```mermaid blocks in markdown in sync with docs/diagrams/*.mmd.

GitHub cannot inline-embed SVG images in markdown, but it renders fenced
```mermaid blocks natively as vector graphics. So docs embed the diagram
source between ``<!-- mermaid:begin NAME -->`` / ``<!-- mermaid:end NAME -->``
markers. This script rewrites those blocks from the ``.mmd`` sources; run it
after editing any ``.mmd`` (``tests/test_docs_diagrams.py`` enforces parity).
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIAGRAMS = ROOT / "docs" / "diagrams"
DOCS = [ROOT / "README.md", ROOT / "README.ru.md",
        *sorted((ROOT / "docs").glob("*/[a-z]*.md"))]
BLOCK = re.compile(r"<!-- mermaid:begin ([\w-]+) -->.*?"
                   r"<!-- mermaid:end \1 -->", re.S)


def render(name: str) -> str:
    src = (DIAGRAMS / f"{name}.mmd").read_text(encoding="utf-8").rstrip("\n")
    return (f"<!-- mermaid:begin {name} -->\n```mermaid\n{src}\n```\n"
            f"<!-- mermaid:end {name} -->")


def main() -> int:
    check = "--check" in sys.argv
    drifted: list[str] = []
    for doc in DOCS:
        if not doc.exists():
            continue
        text = doc.read_text(encoding="utf-8")

        def repl(m: re.Match, doc: Path = doc) -> str:
            new = render(m.group(1))
            if m.group(0) != new:
                drifted.append(f"{doc.relative_to(ROOT)}: {m.group(1)}")
            return new

        new_text = BLOCK.sub(repl, text)
        if not check and new_text != text:
            doc.write_text(new_text, encoding="utf-8")
            print(f"synced {doc.relative_to(ROOT)}")
    if drifted:
        print("diagram blocks out of sync: " + ", ".join(sorted(set(drifted))))
        return 1 if check else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
