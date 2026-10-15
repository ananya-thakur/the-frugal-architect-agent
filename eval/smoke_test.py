"""
eval/smoke_test.py — Lightweight smoke test for the Frugal Architect Agent.

Sends a single low-cost request to an AgentCore endpoint and validates:
  - The agent responds within the timeout.
  - The response is non-empty.
  - (teach mode) The response mentions at least one of the 7 laws.
  - (--assert-attribution) The response attributes Werner Vogels / The Frugal Architect.
  - (codebase-review mode) The response includes a scorecard marker (FIRM or POSSIBLE).

Usage:
    python eval/smoke_test.py \\
        --endpoint https://<agentcore-endpoint> \\
        --mode teach \\
        [--assert-attribution] \\
        [--timeout 60]

    python eval/smoke_test.py \\
        --endpoint https://<agentcore-endpoint> \\
        --mode codebase-review \\
        --timeout 120

Exit codes:
    0 — smoke test passed
    1 — smoke test failed (assertion or timeout)
    2 — configuration error (missing endpoint)
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

# Attribution markers required when --assert-attribution is set (req-attribution).
_ATTRIBUTION_MARKERS = [
    "Werner Vogels",
    "Frugal Architect",
    "thefrugalarchitect.com",
]

# Law markers for teach-mode validation (req-teach-mode).
_LAW_MARKERS = [
    "Law I", "Law II", "Law III", "Law IV", "Law V", "Law VI", "Law VII",
    "Make Cost a Non-Functional Requirement",
    "Systems That Last Align Cost",
    "Architects Must Be Cost-Aware",
    "Unobserved Systems Lead",
    "Cost-Aware Architectures Implement",
    "Cost Optimization Is Incremental",
    "Unchallenged Success Leads",
]

# Scorecard confidence markers for codebase-review mode (adr-003).
_SCORECARD_MARKERS = ["FIRM", "POSSIBLE", "STRONG", "PARTIAL", "GAP", "UNKNOWN"]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Smoke test the Frugal Architect Agent.")
    p.add_argument("--endpoint", required=True, help="AgentCore HTTPS endpoint URL")
    p.add_argument(
        "--mode",
        choices=["teach", "codebase-review", "design-review", "decision-check"],
        default="teach",
        help="Agent mode to exercise (default: teach)",
    )
    p.add_argument(
        "--assert-attribution",
        action="store_true",
        help="Assert that the response attributes Werner Vogels / The Frugal Architect",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=60,
        metavar="SECONDS",
        help="Request timeout in seconds (default: 60)",
    )
    return p.parse_args()


def _build_prompt(mode: str) -> str:
    prompts = {
        "teach": (
            "Explain Law IV of The Frugal Architect — Unobserved Systems Lead to "
            "Unknown Costs. Include the attribution to Werner Vogels."
        ),
        "codebase-review": (
            "I have a small repo with a single Lambda function and a DynamoDB table "
            "defined in CloudFormation. Please run a frugal architecture review."
        ),
        "design-review": (
            "I'm designing a REST API that processes 1,000 requests per day. "
            "How does it score against the Frugal Architecture laws?"
        ),
        "decision-check": (
            "Should I use DynamoDB On-Demand or Provisioned Capacity for a "
            "workload that spikes unpredictably 2–3 times per month? "
            "Evaluate through the most relevant Frugal Architecture laws."
        ),
    }
    return prompts[mode]


def _invoke_agent(endpoint: str, prompt: str, timeout: int) -> str:
    """POST a prompt to the AgentCore endpoint and return the response text."""
    payload = json.dumps({"prompt": prompt}).encode()
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from endpoint: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Request timed out after {timeout}s") from exc

    # AgentCore responses may be plain text or {"response": "..."} JSON.
    try:
        parsed: Any = json.loads(body)
        if isinstance(parsed, dict):
            return str(parsed.get("response") or parsed.get("text") or body)
    except json.JSONDecodeError:
        pass
    return body


def _assert_response(response: str, mode: str, assert_attribution: bool) -> list[str]:
    """Return a list of failure messages; empty list means all assertions passed."""
    failures: list[str] = []

    if not response.strip():
        failures.append("Response is empty")
        return failures

    if mode == "teach":
        if not any(marker in response for marker in _LAW_MARKERS):
            failures.append(
                "Teach response does not mention any of the 7 laws "
                f"(checked: {_LAW_MARKERS[:3]}...)"
            )

    if mode == "codebase-review":
        if not any(marker in response for marker in _SCORECARD_MARKERS):
            failures.append(
                "Codebase-review response contains no confidence/verdict markers "
                "(expected: FIRM, POSSIBLE, STRONG, PARTIAL, GAP, or UNKNOWN)"
            )

    if assert_attribution:
        if not any(marker in response for marker in _ATTRIBUTION_MARKERS):
            failures.append(
                f"Attribution assertion failed: response does not contain any of "
                f"{_ATTRIBUTION_MARKERS} (req-attribution)"
            )

    return failures


def main() -> int:
    args = _parse_args()

    if not args.endpoint.startswith("https://"):
        print(
            f"ERROR: endpoint must start with https:// (got: {args.endpoint})",
            file=sys.stderr,
        )
        return 2

    prompt = _build_prompt(args.mode)
    print(f"Smoke test — mode: {args.mode}, endpoint: {args.endpoint}")
    print(f"Prompt: {prompt[:120]}...")

    try:
        response = _invoke_agent(args.endpoint, prompt, args.timeout)
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"Response received ({len(response)} chars)")

    failures = _assert_response(response, args.mode, args.assert_attribution)
    if failures:
        for msg in failures:
            print(f"FAIL: {msg}", file=sys.stderr)
        return 1

    print("PASS: all smoke test assertions satisfied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
