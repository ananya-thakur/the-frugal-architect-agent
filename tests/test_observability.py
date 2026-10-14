"""
Tests for agent/observability.py — OTEL instrumentation unit (unit-9).

Coverage:
  - setup() idempotency (RULE-OBS-005)
  - setup() fail-open on exception (RULE-OBS-001)
  - setup() degraded-mode on invalid inputs
  - record_cost() correct calculation and negative-token clamping (RULE-OBS-008)
  - emit_eval_metrics() all four dimensions, pass-rate maths, fail-open (RULE-OBS-010/011)
  - mode_from_text() correct labels for all five modes (RULE-OBS-006)

Each test class uses a freshly reloaded module via the `obs` fixture to prevent
module-level singleton state from leaking between tests.
"""

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest


# ── Fixture ────────────────────────────────────────────────────────────────────

def _fresh_observability():
    """Reload agent.observability to reset all module-level singletons."""
    for mod in list(sys.modules):
        if mod == "agent.observability":
            del sys.modules[mod]
    return importlib.import_module("agent.observability")


@pytest.fixture
def obs():
    """Fresh observability module per test — isolates singleton state."""
    mod = _fresh_observability()
    yield mod
    for mod_name in list(sys.modules):
        if mod_name == "agent.observability":
            del sys.modules[mod_name]


_VALID = {
    "endpoint": "http://localhost:4317",
    "service_name": "frugal-architect-agent",
    "model_price": 3e-6,
}


# ── setup() ────────────────────────────────────────────────────────────────────

class TestSetup:
    def test_happy_path_stores_config(self, obs) -> None:
        with patch.object(obs, "_activate_strands_otel"):
            obs.setup(**_VALID)
        assert obs._otel_config is not None
        assert obs._otel_config.endpoint == _VALID["endpoint"]
        assert obs._otel_config.service_name == _VALID["service_name"]
        assert obs._otel_config.model_price_per_token == pytest.approx(3e-6)

    def test_idempotent_second_call_is_noop(self, obs) -> None:
        with patch.object(obs, "_activate_strands_otel") as mock_activate:
            obs.setup(**_VALID)
            obs.setup(**_VALID)  # second call must be a no-op
        assert mock_activate.call_count == 1

    def test_empty_endpoint_triggers_no_op_mode(self, obs) -> None:
        obs.setup(endpoint="", service_name="svc", model_price=1e-6)
        assert obs._otel_config is None

    def test_empty_service_name_triggers_no_op_mode(self, obs) -> None:
        obs.setup(endpoint="http://localhost:4317", service_name="", model_price=1e-6)
        assert obs._otel_config is None

    def test_negative_price_triggers_no_op_mode(self, obs) -> None:
        obs.setup(endpoint="http://localhost:4317", service_name="svc", model_price=-0.001)
        assert obs._otel_config is None

    def test_fail_open_on_activate_exception(self, obs) -> None:
        with patch.object(obs, "_activate_strands_otel", side_effect=RuntimeError("OTEL down")):
            obs.setup(**_VALID)  # must not raise
        assert obs._otel_config is None  # config not committed on failure

    def test_calls_activate_with_correct_args(self, obs) -> None:
        with patch.object(obs, "_activate_strands_otel") as mock_activate:
            obs.setup(**_VALID)
        mock_activate.assert_called_once_with(
            endpoint=_VALID["endpoint"],
            service_name=_VALID["service_name"],
        )


# ── record_cost() ──────────────────────────────────────────────────────────────

class TestRecordCost:
    def _setup(self, obs) -> None:
        with patch.object(obs, "_activate_strands_otel"):
            obs.setup(**_VALID)
        # Inject a mock histogram so we don't need a real OTEL provider
        mock_hist = MagicMock()
        obs._instruments[("histogram", "invocation.cost_usd")] = mock_hist
        obs._meter = MagicMock()
        return mock_hist

    def test_correct_cost_calculation(self, obs) -> None:
        hist = self._setup(obs)
        obs.record_cost(tokens_in=1000, tokens_out=500)
        hist.record.assert_called_once()
        assert hist.record.call_args[0][0] == pytest.approx(1500 * 3e-6)

    def test_clamps_negative_tokens_in(self, obs) -> None:
        hist = self._setup(obs)
        obs.record_cost(tokens_in=-200, tokens_out=500)
        assert hist.record.call_args[0][0] == pytest.approx(500 * 3e-6)

    def test_clamps_negative_tokens_out(self, obs) -> None:
        hist = self._setup(obs)
        obs.record_cost(tokens_in=1000, tokens_out=-50)
        assert hist.record.call_args[0][0] == pytest.approx(1000 * 3e-6)

    def test_both_negative_gives_zero_cost(self, obs) -> None:
        hist = self._setup(obs)
        obs.record_cost(tokens_in=-100, tokens_out=-50)
        assert hist.record.call_args[0][0] == pytest.approx(0.0)

    def test_no_op_without_setup(self, obs) -> None:
        # _otel_config is None → early return, no exception
        obs.record_cost(tokens_in=100, tokens_out=100)
        assert obs._otel_config is None

    def test_fail_open_on_histogram_exception(self, obs) -> None:
        self._setup(obs)
        obs._instruments[("histogram", "invocation.cost_usd")] = MagicMock(
            **{"record.side_effect": RuntimeError("OTEL unreachable")}
        )
        obs.record_cost(tokens_in=100, tokens_out=100)  # must not raise


# ── emit_eval_metrics() ────────────────────────────────────────────────────────

class TestEmitEvalMetrics:
    """
    Uses a _instrument patch to avoid requiring a live OTEL provider
    while still verifying that the correct instruments are created and
    called with the right values (RULE-OBS-010, RULE-OBS-011).
    """

    def _setup_with_mock_instruments(self, obs):
        with patch.object(obs, "_activate_strands_otel"):
            obs.setup(**_VALID)
        obs._meter = MagicMock()
        # Replace _instrument with a version that returns trackable mocks
        store: dict = {}

        def _fake_instrument(kind, name, description="", unit=""):
            if name not in store:
                store[name] = MagicMock()
            return store[name]

        patcher = patch.object(obs, "_instrument", side_effect=_fake_instrument)
        patcher.start()
        return store, patcher

    @staticmethod
    def _uniform_report(obs, pass_count=8, fail_count=2):
        dim = obs.DimensionResult(pass_count=pass_count, fail_count=fail_count)
        return obs.EvalReport(
            groundedness=dim,
            citation_correctness=dim,
            tool_selection=dim,
            unknown_discipline=dim,
        )

    def test_emits_all_four_dimension_counters_and_gauges(self, obs) -> None:
        store, patcher = self._setup_with_mock_instruments(obs)
        try:
            obs.emit_eval_metrics(self._uniform_report(obs))
            dims = ["groundedness", "citation_correctness", "tool_selection", "unknown_discipline"]
            for dim in dims:
                assert f"eval.{dim}.pass_count" in store
                assert f"eval.{dim}.fail_count" in store
                assert f"eval.{dim}.pass_rate" in store
        finally:
            patcher.stop()

    def test_emits_overall_metrics(self, obs) -> None:
        store, patcher = self._setup_with_mock_instruments(obs)
        try:
            obs.emit_eval_metrics(self._uniform_report(obs))
            assert "eval.overall_pass_rate" in store
            assert "eval.total_cases" in store
        finally:
            patcher.stop()

    def test_overall_pass_rate_calculation(self, obs) -> None:
        store, patcher = self._setup_with_mock_instruments(obs)
        try:
            report = obs.EvalReport(
                groundedness=obs.DimensionResult(pass_count=8, fail_count=2),
                citation_correctness=obs.DimensionResult(pass_count=9, fail_count=1),
                tool_selection=obs.DimensionResult(pass_count=7, fail_count=3),
                unknown_discipline=obs.DimensionResult(pass_count=10, fail_count=0),
            )
            obs.emit_eval_metrics(report)
            # overall = (8+9+7+10) / (10+10+10+10) = 34/40 = 0.85
            store["eval.overall_pass_rate"].set.assert_called_once_with(pytest.approx(0.85))
        finally:
            patcher.stop()

    def test_zero_totals_give_zero_overall_rate(self, obs) -> None:
        store, patcher = self._setup_with_mock_instruments(obs)
        try:
            report = obs.EvalReport(
                groundedness=obs.DimensionResult(0, 0),
                citation_correctness=obs.DimensionResult(0, 0),
                tool_selection=obs.DimensionResult(0, 0),
                unknown_discipline=obs.DimensionResult(0, 0),
            )
            obs.emit_eval_metrics(report)
            store["eval.overall_pass_rate"].set.assert_called_once_with(pytest.approx(0.0))
        finally:
            patcher.stop()

    def test_no_op_without_setup(self, obs) -> None:
        # _otel_config is None → early return, no exception
        obs.emit_eval_metrics(
            obs.EvalReport(
                groundedness=obs.DimensionResult(5, 5),
                citation_correctness=obs.DimensionResult(5, 5),
                tool_selection=obs.DimensionResult(5, 5),
                unknown_discipline=obs.DimensionResult(5, 5),
            )
        )

    def test_fail_open_on_instrument_exception(self, obs) -> None:
        with patch.object(obs, "_activate_strands_otel"):
            obs.setup(**_VALID)
        obs._meter = MagicMock()
        with patch.object(obs, "_instrument", side_effect=RuntimeError("OTEL down")):
            obs.emit_eval_metrics(
                obs.EvalReport(
                    groundedness=obs.DimensionResult(5, 5),
                    citation_correctness=obs.DimensionResult(5, 5),
                    tool_selection=obs.DimensionResult(5, 5),
                    unknown_discipline=obs.DimensionResult(5, 5),
                )
            )  # must not raise


# ── mode_from_text() ───────────────────────────────────────────────────────────

class TestModeFromText:
    def test_codebase_review_from_https_url(self, obs) -> None:
        assert obs.mode_from_text("Please review https://github.com/example/repo") == "codebase_review"

    def test_codebase_review_from_http_url(self, obs) -> None:
        assert obs.mode_from_text("Scan http://internal.git/myrepo") == "codebase_review"

    def test_codebase_review_from_git_suffix(self, obs) -> None:
        assert obs.mode_from_text("Analyze this project at /path/to/thing.git") == "codebase_review"

    def test_codebase_review_from_repo_path_segment(self, obs) -> None:
        assert obs.mode_from_text("Clone /repo/frugal-agent and analyze") == "codebase_review"

    def test_codebase_review_from_repository_keyword(self, obs) -> None:
        assert obs.mode_from_text("Analyze this repository for frugality") == "codebase_review"

    def test_design_review_from_design_keyword(self, obs) -> None:
        assert obs.mode_from_text("Here is my design for the microservice") == "design_review"

    def test_design_review_from_describe_your_system(self, obs) -> None:
        assert obs.mode_from_text("Let me describe your system in detail") == "design_review"

    def test_decision_check_from_should_i(self, obs) -> None:
        assert obs.mode_from_text("Should I use Lambda or EC2 for this?") == "decision_check"

    def test_decision_check_from_evaluate_this_decision(self, obs) -> None:
        assert obs.mode_from_text("Please evaluate this decision to use DynamoDB") == "decision_check"

    def test_teach_from_explain_law(self, obs) -> None:
        assert obs.mode_from_text("Can you explain law 3 of frugal architecture?") == "teach"

    def test_teach_from_teach_me(self, obs) -> None:
        assert obs.mode_from_text("Teach me about the frugal architect principles") == "teach"

    def test_unknown_for_unrecognized_text(self, obs) -> None:
        assert obs.mode_from_text("Hello, what can you help me with?") == "unknown"

    def test_unknown_for_empty_string(self, obs) -> None:
        assert obs.mode_from_text("") == "unknown"

    def test_case_insensitive_matching(self, obs) -> None:
        assert obs.mode_from_text("SHOULD I use SQS?") == "decision_check"
        assert obs.mode_from_text("TEACH ME about Law IV") == "teach"
