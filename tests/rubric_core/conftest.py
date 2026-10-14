"""Shared fixtures for rubric-core tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import agent.rubric_core.engine as _engine

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


# ── Cache isolation ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_rubric_cache() -> None:
    """Reset module-level engine state before every test for isolation."""
    _engine._reset_cache()
    yield
    _engine._reset_cache()


# ── Minimal valid rubric fixture ──────────────────────────────────────────────

def _make_minimal_rubric_data() -> dict:
    """Return a minimal but fully-valid rubric dict (7 laws, 10 signals)."""
    return {
        "version": "1.0",
        "attribution": (
            "The 7 Laws of Frugal Architecture by Werner Vogels / "
            "The Frugal Architect (https://thefrugalarchitect.com/laws/)"
        ),
        "laws": [
            {
                "number": i,
                "title": f"Law {i} Title",
                "description": f"Description for law {i}.",
                "strong_looks_like": f"Strong state for law {i}.",
                "probing_questions": [f"Question for law {i}?"],
                "v2_verifiable_signals": [],
            }
            for i in range(1, 8)
        ],
        "anti_pattern_signals": [
            {
                "id": sid,
                "name": f"Signal {n}",
                "description": f"Description for signal {n}.",
                "matching_criteria": f"Criteria for signal {n}.",
                "related_laws": [1],
            }
            for n, sid in enumerate(_SIGNAL_IDS, 1)
        ],
    }


def _make_minimal_rubric_yaml() -> str:
    return yaml.dump(_make_minimal_rubric_data(), default_flow_style=False, allow_unicode=True)


@pytest.fixture()
def minimal_rubric_yaml() -> str:
    """Minimal valid rubric YAML as a string."""
    return _make_minimal_rubric_yaml()


@pytest.fixture()
def rubric_yaml_file(tmp_path: Path, minimal_rubric_yaml: str) -> Path:
    """Write a minimal valid rubric.yaml to a temporary file and return its path."""
    p = tmp_path / "rubric.yaml"
    p.write_text(minimal_rubric_yaml, encoding="utf-8")
    return p
