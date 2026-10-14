"""
Runtime configuration loader for the Frugal Architect Agent.

Raises RuntimeError at import time (startup) for any required variable that is
absent, so the container fails fast before accepting any invocation rather than
silently degrading mid-conversation.  See security-design SC-01 / SC-02.
"""

import os


def _require(name: str) -> str:
    """Return the value of a required environment variable or raise on startup."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Required environment variable '{name}' is not set. "
            "Configure it in the AgentCore deployment descriptor before starting the agent."
        )
    return value


def _require_float(name: str) -> float:
    raw = _require(name)
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"Environment variable '{name}' must be a float, got: {raw!r}"
        ) from exc


# ── Required at startup (SC-01) ──────────────────────────────────────────────
AGENT_MODEL_ID: str = _require("AGENT_MODEL_ID")
MODEL_PRICE_PER_TOKEN: float = _require_float("MODEL_PRICE_PER_TOKEN")
OTEL_EXPORTER_OTLP_ENDPOINT: str = _require("OTEL_EXPORTER_OTLP_ENDPOINT")

# ── Optional with safe defaults ──────────────────────────────────────────────
RUBRIC_PATH: str = os.environ.get("RUBRIC_PATH", "agent/rubric.yaml")
SCAN_FILE_LIMIT: int = int(os.environ.get("SCAN_FILE_LIMIT", "500"))
SCAN_TOKEN_BUDGET: int = int(os.environ.get("SCAN_TOKEN_BUDGET", "100000"))
OTEL_SERVICE_NAME: str = os.environ.get("OTEL_SERVICE_NAME", "frugal-architect-agent")

# AgentCore Memory settings (optional; AgentCore platform injects when enabled)
AGENTCORE_MEMORY_ID: str | None = os.environ.get("AGENTCORE_MEMORY_ID")
AGENTCORE_MEMORY_REGION: str | None = os.environ.get("AGENTCORE_MEMORY_REGION")
