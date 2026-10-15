"""
Tests for agent/config.py — startup environment variable validation.

These tests exercise the fail-fast env loader (security-design SC-01/SC-02)
without requiring Strands, Bedrock, or OTEL to be installed.
"""

import importlib
import sys
from typing import Generator

import pytest


def _reload_config(env: dict) -> object:
    """Reload agent.config in a modified environment and return the module."""
    # Unload both the config module and any module that imported it
    for mod in list(sys.modules.keys()):
        if mod in ("agent.config", "agent"):
            del sys.modules[mod]

    import os

    original = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(env)
        return importlib.import_module("agent.config")
    finally:
        os.environ.clear()
        os.environ.update(original)
        for mod in list(sys.modules.keys()):
            if mod in ("agent.config", "agent"):
                del sys.modules[mod]


_REQUIRED = {
    "AGENT_MODEL_ID": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "MODEL_PRICE_PER_TOKEN": "0.000003",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
}


class TestRequiredVariables:
    def test_happy_path_loads_all_required(self) -> None:
        cfg = _reload_config(_REQUIRED)
        assert cfg.AGENT_MODEL_ID == _REQUIRED["AGENT_MODEL_ID"]
        assert cfg.MODEL_PRICE_PER_TOKEN == pytest.approx(3e-6)
        assert cfg.OTEL_EXPORTER_OTLP_ENDPOINT == _REQUIRED["OTEL_EXPORTER_OTLP_ENDPOINT"]

    def test_missing_agent_model_id_raises(self) -> None:
        env = {k: v for k, v in _REQUIRED.items() if k != "AGENT_MODEL_ID"}
        with pytest.raises(RuntimeError, match="AGENT_MODEL_ID"):
            _reload_config(env)

    def test_missing_model_price_raises(self) -> None:
        env = {k: v for k, v in _REQUIRED.items() if k != "MODEL_PRICE_PER_TOKEN"}
        with pytest.raises(RuntimeError, match="MODEL_PRICE_PER_TOKEN"):
            _reload_config(env)

    def test_missing_otel_endpoint_raises(self) -> None:
        env = {k: v for k, v in _REQUIRED.items() if k != "OTEL_EXPORTER_OTLP_ENDPOINT"}
        with pytest.raises(RuntimeError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
            _reload_config(env)

    def test_empty_string_agent_model_id_raises(self) -> None:
        env = {**_REQUIRED, "AGENT_MODEL_ID": ""}
        with pytest.raises(RuntimeError, match="AGENT_MODEL_ID"):
            _reload_config(env)

    def test_non_float_model_price_raises(self) -> None:
        env = {**_REQUIRED, "MODEL_PRICE_PER_TOKEN": "not-a-number"}
        with pytest.raises(RuntimeError, match="MODEL_PRICE_PER_TOKEN"):
            _reload_config(env)


class TestOptionalVariables:
    def test_defaults_applied_when_optional_absent(self) -> None:
        cfg = _reload_config(_REQUIRED)
        assert cfg.RUBRIC_PATH == "agent/rubric.yaml"
        assert cfg.SCAN_FILE_LIMIT == 500
        assert cfg.SCAN_TOKEN_BUDGET == 100_000
        assert cfg.OTEL_SERVICE_NAME == "frugal-architect-agent"

    def test_custom_rubric_path_accepted(self) -> None:
        env = {**_REQUIRED, "RUBRIC_PATH": "/custom/rubric.yaml"}
        cfg = _reload_config(env)
        assert cfg.RUBRIC_PATH == "/custom/rubric.yaml"

    def test_custom_scan_limits_accepted(self) -> None:
        env = {**_REQUIRED, "SCAN_FILE_LIMIT": "100", "SCAN_TOKEN_BUDGET": "50000"}
        cfg = _reload_config(env)
        assert cfg.SCAN_FILE_LIMIT == 100
        assert cfg.SCAN_TOKEN_BUDGET == 50_000

    def test_agentcore_memory_defaults_to_none(self) -> None:
        cfg = _reload_config(_REQUIRED)
        assert cfg.AGENTCORE_MEMORY_ID is None
        assert cfg.AGENTCORE_MEMORY_REGION is None
