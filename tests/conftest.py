"""
Pytest fixtures for vector DB and similarity tests.

Uses a temporary ChromaDB path to avoid polluting the main database.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

# Ensure project root on path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def temp_vector_db_path(monkeypatch):
    """Use a temporary directory for ChromaDB during tests."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "chromadb"
        monkeypatch.setattr(
            "utils.vector_db._get_db_path",
            lambda: path,
        )
        yield path
