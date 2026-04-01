"""Tests for the local TFV text loader."""
from __future__ import annotations

from typing import TYPE_CHECKING

from agentgolem.runtime.tfv_loader import load_tfv_document, load_tfv_documents

if TYPE_CHECKING:
    from pathlib import Path


def test_load_tfv_documents_returns_sorted_metadata(tmp_path: Path) -> None:
    tfv_dir = tmp_path / "tfv"
    tfv_dir.mkdir()
    (tfv_dir / "beta_note.txt").write_text("beta body", encoding="utf-8")
    (tfv_dir / "alpha_note.txt").write_text("alpha body", encoding="utf-8")

    docs = load_tfv_documents(tmp_path)

    assert [doc.filename for doc in docs] == ["alpha_note.txt", "beta_note.txt"]
    assert docs[0].slug == "alpha_note"
    assert docs[0].title == "Alpha Note"
    assert docs[0].text == "alpha body"


def test_load_tfv_document_returns_matching_file(tmp_path: Path) -> None:
    tfv_dir = tmp_path / "tfv"
    tfv_dir.mkdir()
    (tfv_dir / "five_vows.txt").write_text("whole text", encoding="utf-8")

    doc = load_tfv_document(tmp_path, "five_vows.txt")

    assert doc is not None
    assert doc.filename == "five_vows.txt"
    assert doc.title == "Five Vows"
    assert doc.text == "whole text"


def test_load_tfv_documents_returns_empty_when_directory_missing(tmp_path: Path) -> None:
    assert load_tfv_documents(tmp_path) == []
