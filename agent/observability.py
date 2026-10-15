"""
OTEL instrumentation for the Frugal Architect Agent (unit-9).

All instrumentation uses Strands' built-in OTEL hooks (ADR-006, RULE-OBS-004).
setup() must be called once at agent startup, before the Strands agent is
instantiated, so Strands picks up the OTEL configuration when it initialises
its built-in provider.

Fail-open by design (RULE-OBS-001): every OTEL call is wrapped in try/except;
telemetry failures never propagate to the user-facing chat response.
"""

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_METER_NAME = "frugal-architect-agent"


# ── Domain entities ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OTelConfig:
    """
    Process-lifetime singleton for observability configuration.

    Created once in setup(); subsequent setup() calls are no-ops (RULE-OBS-005).
    Lifecycle: UNINITIALISED → CONFIGURED (success) | FAILED (exception, no-op mode).
    """
    endpoint: str              # OTLP gRPC or HTTP URL
    service_name: str          # resource attribute: service.name
    model_price_per_token: float  # >= 0.0; from MODEL_PRICE_PER_TOKEN env var


@dataclass(frozen=True)
class CostEstimate:
    """
    Transient value object computed inside record_cost(). Not persisted.

    total_cost_usd is always >= 0 because inputs are clamped (RULE-OBS-008).
    """
    tokens_in: int            # clamped to >= 0
    tokens_out: int           # clamped to >= 0
    price_per_token: float    # from OTelConfig.model_price_per_token
    total_cost_usd: float     # = (tokens_in + tokens_out) * price_per_token


@dataclass
class DimensionResult:
    """Eval result for one quality dimension (groundedness, citation, etc.)."""
    pass_count: int
    fail_count: int

    @property
    def total(self) -> int:
        return self.pass_count + self.fail_count

    @property
    def pass_rate(self) -> float:
        return self.pass_count / self.total if self.total > 0 else 0.0


@dataclass
class EvalReport:
    """Aggregated evaluation results across all four quality dimensions."""
    groundedness: DimensionResult
    citation_correctness: DimensionResult
    tool_selection: DimensionResult
    unknown_discipline: DimensionResult


# ── Module-level singletons ────────────────────────────────────────────────────

# Set by setup(); None means setup() has not been called (or failed).
_otel_config: OTelConfig | None = None

# OTEL meter obtained from the Strands-configured global provider (lazy).
_meter = None  # opentelemetry.metrics.Meter | None

# Cached metric instruments keyed by (kind, name) to avoid re-creation.
_instruments: dict = {}


# ── Startup ────────────────────────────────────────────────────────────────────

def setup(endpoint: str, service_name: str, model_price: float) -> None:
    """
    Configure Strands' OTEL provider. Idempotent (RULE-OBS-005). Fail-open (RULE-OBS-001).

    Must be called once before creating the Strands agent so that Strands reads
    the OTEL environment variables when it initialises its built-in provider.

    Invalid inputs (empty strings, negative price) trigger degraded no-op mode:
    the config singleton is not set and telemetry is silently disabled.
    """
    global _otel_config
    if _otel_config is not None:  # RULE-OBS-005: idempotent guard
        return

    if not endpoint or not service_name or model_price < 0:
        logger.debug(
            "observability.setup: invalid inputs; degraded (no-op) mode. "
            "endpoint=%r service_name=%r model_price=%r",
            endpoint, service_name, model_price,
        )
        return

    try:
        _activate_strands_otel(endpoint=endpoint, service_name=service_name)
        _otel_config = OTelConfig(
            endpoint=endpoint,
            service_name=service_name,
            model_price_per_token=max(0.0, model_price),
        )
        logger.debug(
            "observability.setup: configured. service=%r endpoint=%r price_per_token=%r",
            service_name, endpoint, model_price,
        )
    except Exception:  # RULE-OBS-001: fail-open — never surface to user
        logger.debug(
            "observability.setup: exception during OTEL configuration; "
            "agent will run without telemetry",
            exc_info=True,
        )


def _activate_strands_otel(endpoint: str, service_name: str) -> None:
    """
    Ensure Strands' built-in OTEL hooks are activated (ADR-006, RULE-OBS-004).

    Strands reads OTEL configuration from the standard OpenTelemetry environment
    variables when the Agent/BedrockModel is instantiated.  We set those variables
    here so they are guaranteed to be present before the Strands agent is created.

    We do NOT call opentelemetry.trace.get_tracer() or
    opentelemetry.metrics.get_meter() to configure a raw provider here; the global
    OTEL provider lifecycle belongs to Strands (RULE-OBS-004).
    """
    os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint)
    os.environ.setdefault("OTEL_SERVICE_NAME", service_name)

    # Attempt to call Strands' explicit telemetry initialisation path if the SDK
    # exposes one.  If the import fails (e.g. in unit tests, or a Strands version
    # that relies solely on env vars), fall back gracefully.
    try:
        from strands.telemetry import setup_tracing  # type: ignore[import]
        setup_tracing()
    except (ImportError, AttributeError):
        logger.debug(
            "observability: Strands explicit telemetry setup not available; "
            "relying on OTEL env var configuration"
        )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _meter_or_none():
    """
    Return the OTEL meter backed by the Strands-configured global provider.

    Obtained lazily so setup() always runs first.  Returns None if setup() has
    not been called, or if the OTEL provider is unavailable.

    Per RULE-OBS-004, we do not configure a new provider here; we read from the
    one Strands has already registered via _activate_strands_otel().
    """
    global _meter
    if _meter is not None:
        return _meter
    if _otel_config is None:
        return None
    try:
        import opentelemetry.metrics as _otel_metrics  # noqa: PLC0415
        _meter = _otel_metrics.get_meter(_METER_NAME)
    except Exception:  # RULE-OBS-001
        logger.debug("observability: could not obtain OTEL meter", exc_info=True)
    return _meter


def _instrument(kind: str, name: str, description: str = "", unit: str = ""):
    """
    Get or create a cached OTEL metric instrument. Returns None on any failure.

    kind: 'counter' | 'histogram' | 'gauge'
    Caches by (kind, name) so instruments are created at most once per process.
    """
    key = (kind, name)
    if key in _instruments:
        return _instruments[key]
    meter = _meter_or_none()
    if meter is None:
        return None
    try:
        if kind == "counter":
            inst = meter.create_counter(name, description=description, unit=unit)
        elif kind == "histogram":
            inst = meter.create_histogram(name, description=description, unit=unit)
        elif kind == "gauge":
            inst = meter.create_gauge(name, description=description, unit=unit)
        else:
            return None
        _instruments[key] = inst
        return inst
    except Exception:  # RULE-OBS-001
        logger.debug("observability: failed to create instrument %r", name, exc_info=True)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def record_cost(tokens_in: int, tokens_out: int) -> None:
    """
    Compute and emit the estimated cost metric for one model call.

    Clamps negative token counts to zero (RULE-OBS-008).
    No-op if setup() has not been called.
    Fail-open: OTEL failures are caught and logged at DEBUG (RULE-OBS-001).
    """
    if _otel_config is None:
        return
    try:
        t_in = max(0, tokens_in)    # RULE-OBS-008
        t_out = max(0, tokens_out)  # RULE-OBS-008
        estimate = CostEstimate(
            tokens_in=t_in,
            tokens_out=t_out,
            price_per_token=_otel_config.model_price_per_token,
            total_cost_usd=(t_in + t_out) * _otel_config.model_price_per_token,
        )
        hist = _instrument(
            "histogram",
            "invocation.cost_usd",
            description="Estimated cost per invocation in USD",
            unit="USD",
        )
        if hist is not None:
            hist.record(estimate.total_cost_usd)
    except Exception:  # RULE-OBS-001
        logger.debug("observability.record_cost failed", exc_info=True)


def emit_eval_metrics(report: EvalReport) -> None:
    """
    Emit evaluation quality metrics via the Strands-configured OTEL pipeline.

    Reuses the same pipeline as the agent's runtime telemetry (RULE-OBS-010).
    OTEL failure does not affect disk report writing — the two are independent
    (RULE-OBS-011).  No-op if setup() has not been called.  Fail-open (RULE-OBS-001).
    """
    if _otel_config is None:
        logger.debug("observability.emit_eval_metrics: setup() not called; skipping OTEL emission")
        return
    try:
        dims = {
            "groundedness": report.groundedness,
            "citation_correctness": report.citation_correctness,
            "tool_selection": report.tool_selection,
            "unknown_discipline": report.unknown_discipline,
        }
        total_passes = 0
        total_cases = 0

        for dim_name, result in dims.items():
            pass_ctr = _instrument("counter", f"eval.{dim_name}.pass_count",
                                   description=f"Eval pass count — {dim_name}")
            fail_ctr = _instrument("counter", f"eval.{dim_name}.fail_count",
                                   description=f"Eval fail count — {dim_name}")
            rate_gauge = _instrument("gauge", f"eval.{dim_name}.pass_rate",
                                     description=f"Eval pass rate — {dim_name}")

            if pass_ctr is not None:
                pass_ctr.add(result.pass_count)
            if fail_ctr is not None:
                fail_ctr.add(result.fail_count)
            if rate_gauge is not None:
                rate_gauge.set(result.pass_rate)

            total_passes += result.pass_count
            total_cases += result.total

        overall_rate = total_passes / total_cases if total_cases > 0 else 0.0

        overall_gauge = _instrument("gauge", "eval.overall_pass_rate",
                                    description="Overall eval pass rate across all dimensions")
        total_ctr = _instrument("counter", "eval.total_cases",
                                description="Total test cases executed in harness run")

        if overall_gauge is not None:
            overall_gauge.set(overall_rate)
        if total_ctr is not None:
            total_ctr.add(total_cases)

    except Exception:  # RULE-OBS-001
        logger.debug("observability.emit_eval_metrics failed", exc_info=True)


def mode_from_text(text: str) -> str:
    """
    Derive invocation.mode from message text (best-effort heuristic, RULE-OBS-006).

    Returns one of: codebase_review | design_review | decision_check | teach | unknown.
    Mode MUST NOT affect any functional behaviour — it is a trace label only.
    Defaults to 'unknown' when no heuristic matches (RULE-OBS-006).
    """
    low = text.lower()
    if any(k in low for k in ("http://", "https://", ".git", "/repo", "repository")):
        return "codebase_review"
    if "design" in low or "describe your system" in low:
        return "design_review"
    if "should i" in low or "evaluate this decision" in low:
        return "decision_check"
    if "explain law" in low or "teach me" in low:
        return "teach"
    return "unknown"
