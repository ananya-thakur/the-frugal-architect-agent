"""
eval/assert_eval.py — Assert eval harness pass rate meets the minimum threshold.

Usage:
    python eval/assert_eval.py <report-file.json> --min-pass-rate 0.75

Reads a JSON eval report produced by run_eval.py and exits non-zero when the
overall pass rate across all scored dimensions is below --min-pass-rate.

Exit codes:
    0 — pass rate meets or exceeds threshold (gate passes)
    1 — pass rate is below threshold (gate fails; CD blocks staging promotion)
    2 — report file not found or malformed JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Assert eval report meets minimum pass rate.")
    p.add_argument("report", help="Path to the JSON eval report produced by run_eval.py")
    p.add_argument(
        "--min-pass-rate",
        type=float,
        default=0.75,
        metavar="RATE",
        help="Minimum pass rate in [0,1] (default: 0.75)",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    report_path = Path(args.report)

    if not report_path.exists():
        print(f"ERROR: report file not found: {report_path}", file=sys.stderr)
        return 2

    try:
        report = json.loads(report_path.read_text())
    except json.JSONDecodeError as exc:
        print(f"ERROR: malformed JSON in {report_path}: {exc}", file=sys.stderr)
        return 2

    # The report schema matches eval/models.py EvalReport:
    #   { "overall_verdict": "PASS"|"FAIL",
    #     "dimensions": [{"dimension": str, "pass_rate": float, ...}, ...] }
    dimensions = report.get("dimensions", [])
    if not dimensions:
        print("ERROR: report contains no dimension results", file=sys.stderr)
        return 2

    total_rate = sum(d.get("pass_rate", 0.0) for d in dimensions) / len(dimensions)
    threshold = args.min_pass_rate

    print(f"Eval pass rate: {total_rate:.1%} (threshold: {threshold:.0%})")
    for d in dimensions:
        status = "PASS" if d.get("pass_rate", 0.0) >= threshold else "FAIL"
        print(f"  [{status}] {d.get('dimension', 'unknown')}: {d.get('pass_rate', 0.0):.1%}")

    if total_rate < threshold:
        print(
            f"\nFAIL: overall pass rate {total_rate:.1%} is below minimum {threshold:.0%}",
            file=sys.stderr,
        )
        return 1

    print(f"\nPASS: overall pass rate {total_rate:.1%} meets minimum {threshold:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
