"""
EvalReportSerializer — writes sanitized Markdown and JSON eval reports.

Content policy (security-design-eval-harness SR-EH-005):
  Reports contain ONLY: run_id, timestamp, test counts, verdicts, test_id strings.
  Reports MUST NOT contain: fixture paths, scenario descriptions, raw agent responses.

EvalReportSerializer is the ONLY code path that writes report files; run_eval.py
calls it and never writes report content directly.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from eval.models import EvalReport


class EvalReportSerializer:
    """Transforms an EvalReport into sanitized Markdown or JSON on disk."""

    def write_markdown(self, report: EvalReport, output_path: Path) -> None:
        """Write a sanitized Markdown report to output_path."""
        output_path.write_text(self._to_markdown(report), encoding="utf-8")

    def write_json(self, report: EvalReport, output_path: Path) -> None:
        """Write a sanitized JSON report to output_path."""
        output_path.write_text(self._to_json(report), encoding="utf-8")

    # ── Serializers ───────────────────────────────────────────────────────────

    def _to_markdown(self, report: EvalReport) -> str:
        lines: list[str] = [
            "# Frugal Architect Agent — Eval Report",
            f"Run: {report.run_id} | {report.timestamp} | Verdict: {report.overall_verdict}",
            "",
            "## Dimension Scores",
            "| Dimension | Pass | Fail | Rate |",
            "|-----------|------|------|------|",
        ]
        for dim in report.dimensions:
            rate_pct = f"{dim.pass_rate * 100:.0f}%"
            lines.append(
                f"| {dim.dimension} | {dim.pass_count} | {dim.fail_count} | {rate_pct} |"
            )

        failing = [a for dim in report.dimensions for a in dim.failing_assertions]
        lines.append("")
        lines.append("## Failing Assertions")
        if failing:
            for a in failing:
                # Only test_id, dimension, expected, actual — no fixture/response content
                lines.append(
                    f"- [{a.test_id}] dimension={a.dimension} "
                    f"expected={a.expected} actual={a.actual}"
                )
        else:
            lines.append("*(none)*")

        lines += [
            "",
            f"**Total cases:** {report.total_cases}",
            f"**Overall verdict:** {report.overall_verdict}",
            "",
        ]
        return "\n".join(lines)

    def _to_json(self, report: EvalReport) -> str:
        # dataclasses.asdict converts nested dataclasses recursively
        return json.dumps(asdict(report), indent=2)
