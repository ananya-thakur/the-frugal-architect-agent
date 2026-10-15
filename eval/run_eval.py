"""
eval/run_eval.py — Frugal Architect Agent evaluation harness runner.

Usage:
    python eval/run_eval.py [--endpoint URL] [--test-set PATH] [--output-dir PATH]

    --endpoint    AgentCore HTTPS endpoint. Omit to invoke the local Strands
                  agent directly (offline / developer mode).
    --test-set    Directory containing test_cases.yaml and fixture repos.
                  Default: eval/fixtures
    --output-dir  Directory for Markdown and JSON eval reports.
                  Default: eval/reports

Exit codes:
    0 — all dimensions PASS
    1 — at least one dimension FAIL
    2 — fixture security violation (FixtureSecurityViolation raised on startup)

Security controls applied (security-design-eval-harness):
    SR-EH-001  fixture_validator runs before any test execution
    SR-EH-003  per-run UUID memory namespace or in-process stub
    SR-EH-004  metrics emitted only via observability.emit_eval_metrics
    SR-EH-005  reports written only via EvalReportSerializer
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml

# Ensure the repo root is on sys.path when executed directly as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import observability as _obs
from agent.observability import DimensionResult as ObsDimResult
from agent.observability import EvalReport as ObsEvalReport
from eval.evaluator import aggregate_results, evaluate_test_case
from eval.fixture_validator import FixtureSecurityViolation, validate_fixtures
from eval.models import (
    CitationCorrectnessAssertion,
    EvalReport,
    GroundednessAssertion,
    TestCase,
    TestCaseAssertions,
    ToolSelectionAssertion,
    UnknownDisciplineAssertion,
)
from eval.report_writer import EvalReportSerializer

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval")


# ── Test case loading ─────────────────────────────────────────────────────────


def load_test_cases(fixture_dir: Path) -> list[TestCase]:
    """Parse test_cases.yaml from fixture_dir into a list of TestCase objects."""
    tc_path = fixture_dir / "test_cases.yaml"
    if not tc_path.exists():
        raise FileNotFoundError(f"test_cases.yaml not found at {tc_path}")

    raw = yaml.safe_load(tc_path.read_text(encoding="utf-8"))
    cases: list[TestCase] = []
    for item in raw.get("test_cases", []):
        cases.append(
            TestCase(
                test_id=item["test_id"],
                description=item.get("description", ""),
                input_text=item.get("input_text", ""),
                fixture_repo=item.get("fixture_repo"),
                assertions=_parse_assertions(item.get("assertions", {})),
            )
        )
    return cases


def _parse_assertions(raw: dict[str, object]) -> TestCaseAssertions:
    def _g(key: str, cls: type) -> object | None:  # type: ignore[type-arg]
        v = raw.get(key)
        return cls(**v) if isinstance(v, dict) else None  # type: ignore[call-arg]

    return TestCaseAssertions(
        groundedness=_g("groundedness", GroundednessAssertion),  # type: ignore[arg-type]
        citation_correctness=_g("citation_correctness", CitationCorrectnessAssertion),  # type: ignore[arg-type]
        tool_selection=_g("tool_selection", ToolSelectionAssertion),  # type: ignore[arg-type]
        unknown_discipline=_g("unknown_discipline", UnknownDisciplineAssertion),  # type: ignore[arg-type]
    )


# ── Agent invocation ──────────────────────────────────────────────────────────


def _build_input_message(test_case: TestCase) -> str:
    """Combine input_text with fixture_repo path (if any)."""
    if test_case.fixture_repo:
        fixture_path = Path(test_case.fixture_repo).resolve()
        return f"{test_case.input_text}\n\nRepo path: {fixture_path}"
    return test_case.input_text


def _invoke_via_http(endpoint: str, message: str, session_id: str) -> str:
    """Call the deployed AgentCore agent over HTTPS. Returns response text."""
    import json as _json
    import urllib.request

    payload = _json.dumps({"message": message, "sessionId": session_id}).encode()
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        body = _json.loads(resp.read().decode())
    return str(body.get("response", body.get("content", body)))


def _invoke_local(message: str) -> str:
    """Invoke the local Strands agent directly (offline/developer mode)."""
    try:
        from agent.agent_assembly import create_agent  # type: ignore[import]

        agent = create_agent()
        return str(agent(message))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Local agent invocation failed: %s", exc)
        return f"ERROR: {exc}"


def invoke_agent(endpoint: str | None, test_case: TestCase, session_id: str) -> str:
    """Dispatch to HTTP or local agent based on whether endpoint is configured."""
    message = _build_input_message(test_case)
    if endpoint:
        return _invoke_via_http(endpoint, message, session_id)
    return _invoke_local(message)


# ── Memory isolation ──────────────────────────────────────────────────────────


def _setup_memory_isolation(run_id: str) -> None:
    """
    Inject a per-run AgentCore Memory namespace prefix (SR-EH-003).

    When AgentCore Memory is unavailable (the default in offline CI), logs a
    warning. The in-process stub activated by the memory-manager at runtime
    ensures no real AgentCore calls are made.
    """
    available = os.environ.get("AGENTCORE_MEMORY_AVAILABLE", "false").lower() == "true"
    if available:
        os.environ["AGENTCORE_MEMORY_NAMESPACE_PREFIX"] = f"eval-harness-{run_id}"
        logger.info("Memory: AgentCore namespace prefix=eval-harness-%s", run_id)
    else:
        logger.warning(
            "memory_stub active — eval sessions are not persisted to AgentCore Memory"
        )


# ── OTEL metrics ──────────────────────────────────────────────────────────────


def _emit_otel_metrics(report: EvalReport) -> None:
    """
    Emit aggregated eval metrics via observability.emit_eval_metrics (SR-EH-004).

    Only called when OTEL_EVAL_EXPORT=true and OTLP_ENDPOINT is set.
    Failure is silent — disk report already written independently (RULE-OBS-011).
    """
    otel_export = os.environ.get("OTEL_EVAL_EXPORT", "false").lower() == "true"
    otlp_endpoint = os.environ.get("OTLP_ENDPOINT", "")
    if not (otel_export and otlp_endpoint):
        return

    _obs.setup(
        endpoint=otlp_endpoint,
        service_name="frugal-architect-agent-eval",
        model_price=0.0,
    )

    dims = {d.dimension: d for d in report.dimensions}

    def _dim(name: str) -> ObsDimResult:
        d = dims.get(name)
        return ObsDimResult(
            pass_count=d.pass_count if d else 0,
            fail_count=d.fail_count if d else 0,
        )

    _obs.emit_eval_metrics(
        ObsEvalReport(
            groundedness=_dim("groundedness"),
            citation_correctness=_dim("citation_correctness"),
            tool_selection=_dim("tool_selection"),
            unknown_discipline=_dim("unknown_discipline"),
        )
    )


# ── Main evaluation loop ──────────────────────────────────────────────────────


def run_eval(
    endpoint: str | None,
    fixture_dir: Path,
    output_dir: Path,
) -> EvalReport:
    """
    Execute the full evaluation pipeline.

    Returns the EvalReport. Side-effects: writes reports to output_dir,
    optionally emits OTEL metrics.
    """
    run_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now(tz=UTC).isoformat()
    logger.info("eval-harness run=%s starting", run_id)

    # SR-EH-001: security gate before any test execution
    try:
        validate_fixtures(fixture_dir)
    except FixtureSecurityViolation as exc:
        logger.error("FIXTURE SECURITY VIOLATION — aborting: %s", exc)
        sys.exit(2)

    # SR-EH-003: per-run memory namespace
    _setup_memory_isolation(run_id)

    # Load and run test cases
    test_cases = load_test_cases(fixture_dir)
    logger.info("Loaded %d test cases", len(test_cases))

    case_results = []
    for tc in test_cases:
        session_id = f"eval-{run_id}-{tc.test_id}"
        logger.info("Running: %s", tc.test_id)
        try:
            response = invoke_agent(endpoint, tc, session_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Test case %s raised: %s", tc.test_id, exc)
            response = f"ERROR: {exc}"
        case_results.append(evaluate_test_case(tc, response))

    # Aggregate and build report
    dim_results = aggregate_results(case_results)
    overall_verdict = "PASS" if all(d.fail_count == 0 for d in dim_results.values()) else "FAIL"

    report = EvalReport(
        run_id=run_id,
        timestamp=timestamp,
        total_cases=len(test_cases),
        overall_verdict=overall_verdict,
        dimensions=list(dim_results.values()),
    )

    # SR-EH-005: write reports through serializer only
    output_dir.mkdir(parents=True, exist_ok=True)
    serializer = EvalReportSerializer()
    md_path = output_dir / f"eval-report-{run_id}.md"
    json_path = output_dir / f"eval-report-{run_id}.json"
    serializer.write_markdown(report, md_path)
    serializer.write_json(report, json_path)
    logger.info("Reports: %s  %s", md_path, json_path)

    # SR-EH-004: emit OTEL metrics (fail-open, independent of report write)
    _emit_otel_metrics(report)

    return report


# ── CLI entry point ───────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Frugal Architect Agent — offline evaluation harness"
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("AGENTCORE_ENDPOINT", ""),
        help="AgentCore HTTPS endpoint (omit for local/offline mode)",
    )
    parser.add_argument(
        "--test-set",
        default="eval/fixtures",
        metavar="PATH",
        help="Fixture directory containing test_cases.yaml",
    )
    parser.add_argument(
        "--output-dir",
        default="eval/reports",
        metavar="PATH",
        help="Directory for eval report files",
    )
    args = parser.parse_args()

    endpoint: str | None = args.endpoint or None
    report = run_eval(
        endpoint=endpoint,
        fixture_dir=Path(args.test_set),
        output_dir=Path(args.output_dir),
    )

    print(f"\nEval run {report.run_id} — {report.overall_verdict}")
    print(f"Total cases: {report.total_cases}")
    for dim in report.dimensions:
        total = dim.pass_count + dim.fail_count
        print(f"  {dim.dimension}: {dim.pass_count}/{total} ({dim.pass_rate:.0%})")

    sys.exit(0 if report.overall_verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
