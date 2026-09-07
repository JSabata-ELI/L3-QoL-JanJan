"""Fixtures shared by the SPFE Values tests.

`tmp` is the name the tests in this folder ask for. It is pytest's own
`tmp_path` under a shorter name -- without this file the eleven workbook tests
in test_spfe_store.py error out with "fixture 'tmp' not found" and never run at
all, which is how they came to be written against a shape nobody was checking.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp(tmp_path: Path) -> Path:
    return tmp_path
