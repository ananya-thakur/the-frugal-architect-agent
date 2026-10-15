"""Shared fixtures for iac-scanner tests."""

from __future__ import annotations

from typing import Any

import pytest
import yaml

import agent.rubric_core.engine as _engine
from agent.iac_scanner.models import (
    Confidence,
    File,
    IaCFormat,
    PrioritizedInventory,
)
from agent.rubric_core.models import IaCSignal, LawDefinition, Rubric

# ── Cache isolation ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_rubric_cache() -> None:
    _engine._reset_cache()
    yield
    _engine._reset_cache()


# ── Signal helpers ────────────────────────────────────────────────────────────

_APP_SIGNAL_IDS = [
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


def make_iac_signal(**kwargs: Any) -> IaCSignal:
    """Build a minimal IaCSignal with sensible defaults."""
    defaults: dict[str, Any] = {
        "id": "test-signal",
        "formats": ["cloudformation"],
        "resource_type_filter": None,
        "attribute_path": "Properties.MemorySize",
        "predicate": "> 512",
        "gap_description": "Test gap description.",
        "evidence_template": "{resource_id} in {file_path} value={attribute_value}.",
        "severity_rank": 2,
    }
    defaults.update(kwargs)
    return IaCSignal(**defaults)


def make_rubric_with_signals(signals_per_law: dict[int, list[IaCSignal]] | None = None) -> Rubric:
    """Build a minimal Rubric; optionally attach IaCSignals to specific law numbers."""
    if signals_per_law is None:
        signals_per_law = {}
    laws = [
        LawDefinition(
            number=i,
            title=f"Law {i} Title",
            description=f"Description for law {i}.",
            strong_looks_like=f"Strong state for law {i}.",
            probing_questions=[f"Question for law {i}?"],
            iac_signals=signals_per_law.get(i, []),
        )
        for i in range(1, 8)
    ]
    anti_pattern_signals = [
        {
            "id": sid,
            "name": f"Signal {n}",
            "description": f"Desc {n}.",
            "matching_criteria": f"Criteria {n}.",
            "related_laws": [1],
        }
        for n, sid in enumerate(_APP_SIGNAL_IDS, 1)
    ]
    return Rubric(
        version="1.0",
        attribution="Werner Vogels / The Frugal Architect",
        laws=laws,
        anti_pattern_signals=[
            __import__("agent.rubric_core.models", fromlist=["AntiPatternSignal"]).AntiPatternSignal(**s)
            for s in anti_pattern_signals
        ],
    )


# ── File / inventory builders ─────────────────────────────────────────────────

def make_file(
    path: str,
    content: str,
    fmt: IaCFormat = IaCFormat.CLOUDFORMATION,
) -> File:
    return File(path=path, content=content, format=fmt, size_bytes=len(content))


def make_inventory(*files: File) -> PrioritizedInventory:
    return PrioritizedInventory(
        iac_files=tuple(files),
        app_files=(),
        total_file_count=len(files),
        scan_token_estimate=sum(f.size_bytes for f in files),
    )


# ── CloudFormation template helpers ──────────────────────────────────────────

def cf_template(resources: dict[str, dict]) -> str:
    """Return a minimal CloudFormation YAML template."""
    data = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Resources": resources,
    }
    return yaml.dump(data, default_flow_style=False)


def cf_lambda(logical_id: str, memory: int = 128, tracing: str | None = None,
              reserved: int | None = None, architectures: list | None = None) -> dict:
    """Return a CloudFormation Lambda resource definition."""
    props: dict[str, Any] = {
        "FunctionName": logical_id,
        "Handler": "index.handler",
        "Runtime": "python3.12",
        "Role": "arn:aws:iam::123456789012:role/lambda-role",
        "MemorySize": memory,
    }
    if tracing is not None:
        props["TracingConfig"] = {"Mode": tracing}
    if reserved is not None:
        props["ReservedConcurrentExecutions"] = reserved
    if architectures is not None:
        props["Architectures"] = architectures
    return {"Type": "AWS::Lambda::Function", "Properties": props}
