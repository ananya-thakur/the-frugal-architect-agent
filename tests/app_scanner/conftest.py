"""Shared fixtures for app-scanner tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import agent.rubric_core.engine as _engine
from agent.app_scanner.models import File, PrioritizedInventory
from agent.rubric_core.engine import load_rubric
from agent.rubric_core.models import Rubric


# ── Rubric cache isolation (same pattern as rubric-core tests) ────────────────

@pytest.fixture(autouse=True)
def reset_rubric_cache() -> None:
    _engine._reset_cache()
    yield
    _engine._reset_cache()


# ── Load the real bundled rubric ──────────────────────────────────────────────

@pytest.fixture()
def real_rubric(tmp_path: Path) -> Rubric:
    """Return the real rubric loaded from the bundled rubric.yaml."""
    from agent.rubric_core import engine
    rubric_path = Path(engine.__file__).parent / "rubric.yaml"
    return load_rubric(rubric_path)


# ── Minimal rubric (7 stub laws, 10 real signal IDs) ─────────────────────────

_SIGNAL_IDS = [
    "n-plus-one-queries",
    "missing-pagination",
    "full-table-scans",
    "polling-instead-of-events",
    "unbounded-aggressive-retries",
    "no-caching-on-hot-paths",
    "over-fetching-oversized-payloads",
    "sync-where-async-possible",
    "per-request-heavy-initialization",
    "whole-object-single-field-read",
]


def _minimal_rubric_data() -> dict:
    return {
        "version": "1.0",
        "attribution": "Test attribution",
        "laws": [
            {
                "number": i,
                "title": f"Law {i} Title",
                "description": f"Law {i} description.",
                "strong_looks_like": f"Strong state for law {i}.",
                "probing_questions": [f"Question {i}?"],
                "v2_verifiable_signals": [],
            }
            for i in range(1, 8)
        ],
        "anti_pattern_signals": [
            {
                "id": sid,
                "name": sid.replace("-", " ").title(),
                "description": f"Description for {sid}.",
                "matching_criteria": f"Criteria for {sid}.",
                "related_laws": [1],
            }
            for sid in _SIGNAL_IDS
        ],
    }


@pytest.fixture()
def minimal_rubric(tmp_path: Path) -> Rubric:
    data = _minimal_rubric_data()
    rubric_path = tmp_path / "rubric.yaml"
    rubric_path.write_text(yaml.dump(data), encoding="utf-8")
    return load_rubric(rubric_path)


# ── File / Inventory helpers ──────────────────────────────────────────────────

def make_py_file(path: str, content: str) -> File:
    return File(path=path, content=content, extension=".py", size_bytes=len(content.encode()))


def make_inventory(app_files: list[File], iac_files: list[File] | None = None) -> PrioritizedInventory:
    iac = iac_files or []
    return PrioritizedInventory(
        iac_files=iac,
        app_files=app_files,
        files_scanned_count=len(app_files) + len(iac),
        files_skipped_count=0,
        scan_cap_reached=False,
        repo_root="/repo",
    )
