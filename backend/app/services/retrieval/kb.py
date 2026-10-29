"""Policy knowledge base: markdown files chunked by '## ' headings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class KbChunk:
    chunk_id: str  # "<file_stem>#<heading-slug>"
    doc_title: str
    heading: str
    text: str


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_kb(kb_dir: Path) -> list[KbChunk]:
    chunks: list[KbChunk] = []
    for path in sorted(kb_dir.glob("*.md")):
        doc_title = path.stem.replace("_", " ").title()
        heading: str | None = None
        body: list[str] = []

        def flush() -> None:
            if heading is not None:
                text = "\n".join(body).strip()
                chunks.append(
                    KbChunk(chunk_id=f"{path.stem}#{slugify(heading)}", doc_title=doc_title, heading=heading, text=text)
                )

        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                doc_title = line[2:].strip()
            elif line.startswith("## "):
                flush()
                heading = line[3:].strip()
                body = []
            elif heading is not None:
                body.append(line)
        flush()
    return chunks


def kb_index(kb_dir: Path) -> dict[str, KbChunk]:
    return {c.chunk_id: c for c in load_kb(kb_dir)}
