"""
Tests for eval/report_writer.py — sanitized eval report serialization.

Coverage:
  - write_markdown: produces correct headers, dimension table, failing assertions
  - write_json: round-trips to/from JSON without data loss
  - Content policy: fixture paths, scenario descriptions, agent responses are absent
  - Reports with no failures render "(none)" in the failing assertions section
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.models import DimensionResult, EvalReport, FailingAssertion
from eval.report_writer import EvalReportSerializer


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_report(overall: str = "PASS") -> EvalReport:
    dims = [
        DimensionResult(
            dimension="groundedness",
            pass_count=2,
            fail_count=0,
            pass_rate=1.0,
            failing_case_ids=[],
            failing_assertions=[],
        ),
        DimensionResult(
            dimension="citation_correctness",
            pass_count=1,
            fail_count=1,
            pass_rate=0.5,
            failing_case_ids=["tc-bad"],
            failing_assertions=[
                FailingAssertion(
                    test_id="tc-bad",
                    dimension="citation_correctness",
                    expected="Law IV cited",
                    actual="Law IV found=False",
                )
            ],
        ),
    ]
    return EvalReport(
        run_id="abc12345",
        timestamp="2026-10-15T00:00:00+00:00",
        total_cases=3,
        overall_verdict=overall,
        dimensions=dims,
    )


# ── Markdown ──────────────────────────────────────────────────────────────────


class TestMarkdownReport:
    def test_header_present(self, tmp_path: Path):
        report = _make_report()
        out = tmp_path / "report.md"
        EvalReportSerializer().write_markdown(report, out)
        content = out.read_text()
        assert "# Frugal Architect Agent — Eval Report" in content

    def test_run_id_and_verdict_in_header(self, tmp_path: Path):
        report = _make_report("FAIL")
        out = tmp_path / "report.md"
        EvalReportSerializer().write_markdown(report, out)
        content = out.read_text()
        assert "abc12345" in content
        assert "FAIL" in content

    def test_dimension_table_rows(self, tmp_path: Path):
        report = _make_report()
        out = tmp_path / "report.md"
        EvalReportSerializer().write_markdown(report, out)
        content = out.read_text()
        assert "groundedness" in content
        assert "citation_correctness" in content
        assert "100%" in content  # groundedness pass rate
        assert "50%" in content   # citation_correctness pass rate

    def test_failing_assertions_listed(self, tmp_path: Path):
        report = _make_report()
        out = tmp_path / "report.md"
        EvalReportSerializer().write_markdown(report, out)
        content = out.read_text()
        assert "tc-bad" in content
        assert "Law IV cited" in content

    def test_no_failures_renders_none(self, tmp_path: Path):
        # Build a report with only passing dimensions
        dims = [
            DimensionResult("groundedness", 2, 0, 1.0, [], []),
        ]
        report = EvalReport("r1", "2026-01-01", 2, "PASS", dims)
        out = tmp_path / "report.md"
        EvalReportSerializer().write_markdown(report, out)
        content = out.read_text()
        assert "*(none)*" in content

    def test_no_fixture_content_in_report(self, tmp_path: Path):
        report = _make_report()
        out = tmp_path / "report.md"
        EvalReportSerializer().write_markdown(report, out)
        content = out.read_text()
        # No fixture paths, scenario text, or raw agent response text
        assert "eval/fixtures/" not in content
        assert "fixture_repo" not in content


# ── JSON ──────────────────────────────────────────────────────────────────────


class TestJsonReport:
    def test_json_valid(self, tmp_path: Path):
        report = _make_report()
        out = tmp_path / "report.json"
        EvalReportSerializer().write_json(report, out)
        data = json.loads(out.read_text())
        assert isinstance(data, dict)

    def test_run_id_roundtrip(self, tmp_path: Path):
        report = _make_report()
        out = tmp_path / "report.json"
        EvalReportSerializer().write_json(report, out)
        data = json.loads(out.read_text())
        assert data["run_id"] == "abc12345"

    def test_dimensions_present(self, tmp_path: Path):
        report = _make_report()
        out = tmp_path / "report.json"
        EvalReportSerializer().write_json(report, out)
        data = json.loads(out.read_text())
        dim_names = [d["dimension"] for d in data["dimensions"]]
        assert "groundedness" in dim_names
        assert "citation_correctness" in dim_names

    def test_failing_assertion_fields(self, tmp_path: Path):
        report = _make_report()
        out = tmp_path / "report.json"
        EvalReportSerializer().write_json(report, out)
        data = json.loads(out.read_text())
        cc = next(d for d in data["dimensions"] if d["dimension"] == "citation_correctness")
        fa = cc["failing_assertions"][0]
        assert set(fa.keys()) == {"test_id", "dimension", "expected", "actual"}

    def test_overall_verdict_present(self, tmp_path: Path):
        report = _make_report("FAIL")
        out = tmp_path / "report.json"
        EvalReportSerializer().write_json(report, out)
        data = json.loads(out.read_text())
        assert data["overall_verdict"] == "FAIL"
