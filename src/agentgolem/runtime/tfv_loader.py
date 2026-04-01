"""Loader for the local TFV text corpus."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class TFVDocument:
    """Stable metadata and text for one local TFV document."""

    filename: str
    slug: str
    title: str
    path: Path
    text: str


def _tfv_dir(repo_root: Path) -> Path:
    return repo_root / "tfv"


def _slugify(stem: str) -> str:
    return stem.strip().lower().replace("-", "_").replace(" ", "_")


def _titleize(stem: str) -> str:
    parts = [part for part in stem.replace("-", "_").split("_") if part]
    return " ".join(part.capitalize() for part in parts)


def load_tfv_documents(repo_root: Path) -> list[TFVDocument]:
    """Load all `tfv\\*.txt` files in stable filename order."""
    base = _tfv_dir(repo_root)
    if not base.is_dir():
        return []

    documents: list[TFVDocument] = []
    for path in sorted(base.glob("*.txt")):
        text = path.read_text(encoding="utf-8").strip()
        documents.append(
            TFVDocument(
                filename=path.name,
                slug=_slugify(path.stem),
                title=_titleize(path.stem),
                path=path,
                text=text,
            )
        )
    return documents


def load_tfv_document(repo_root: Path, filename: str) -> TFVDocument | None:
    """Return one TFV document by filename, if present."""
    for document in load_tfv_documents(repo_root):
        if document.filename == filename:
            return document
    return None
