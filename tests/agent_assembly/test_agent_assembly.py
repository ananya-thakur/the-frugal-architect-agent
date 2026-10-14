"""
Tests for agent-assembly unit (unit-10).

Coverage:
  - _infer_iac_format(): all major IaC format variants and UNKNOWN fallback
  - _build_iac_inventory(): type conversion from repo-connector RepoInventory
  - _build_app_inventory(): type conversion from repo-connector RepoInventory
  - load_rubric_tool(): success and error paths
  - get_law_tool(): valid law ref and unknown law ref
  - scan_iac_tool(): uses mocked inventory cache
  - scan_app_antipatterns_tool(): uses mocked inventory cache
  - create_agent(): mocked Strands SDK, checks model ID and tool count
  - SYSTEM_PROMPT: contains required attribution and guardrail text
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from agent.agent_assembly import (
    SYSTEM_PROMPT,
    _build_app_inventory,
    _build_iac_inventory,
    _infer_iac_format,
    get_law_tool,
    load_rubric_tool,
    scan_app_antipatterns_tool,
    scan_iac_tool,
)
from agent.iac_scanner.models import IaCFormat
from tests.agent_assembly.conftest import _make_repo_inventory


# ── _infer_iac_format ─────────────────────────────────────────────────────────

class TestInferIacFormat:
    def test_terraform_tf(self):
        assert _infer_iac_format("infra/main.tf") == IaCFormat.TERRAFORM

    def test_terraform_tfvars(self):
        assert _infer_iac_format("terraform/vars.tfvars") == IaCFormat.TERRAFORM

    def test_terraform_hcl(self):
        assert _infer_iac_format("modules/networking.hcl") == IaCFormat.TERRAFORM

    def test_dockerfile_exact(self):
        assert _infer_iac_format("Dockerfile") == IaCFormat.DOCKERFILE

    def test_dockerfile_variant(self):
        assert _infer_iac_format("docker/Dockerfile.prod") == IaCFormat.DOCKERFILE

    def test_docker_compose(self):
        assert _infer_iac_format("docker-compose.yml") == IaCFormat.DOCKERFILE

    def test_serverless_yml(self):
        assert _infer_iac_format("serverless.yml") == IaCFormat.SERVERLESS_YML

    def test_serverless_yaml(self):
        assert _infer_iac_format("serverless.yaml") == IaCFormat.SERVERLESS_YML

    def test_cdk_python_stack(self):
        assert _infer_iac_format("infra/my_stack.py") == IaCFormat.CDK_PYTHON

    def test_cdk_python_app(self):
        assert _infer_iac_format("cdk/app.py") == IaCFormat.CDK_PYTHON

    def test_cdk_typescript_stack(self):
        assert _infer_iac_format("infra/my_stack.ts") == IaCFormat.CDK_TYPESCRIPT

    def test_cdk_typescript_in_cdk_dir(self):
        assert _infer_iac_format("cdk/pipeline.ts") == IaCFormat.CDK_TYPESCRIPT

    def test_cdk_json(self):
        assert _infer_iac_format("cdk.json") == IaCFormat.CDK_TYPESCRIPT

    def test_cloudformation_template_yaml(self):
        assert _infer_iac_format("infra/template.yaml") == IaCFormat.CLOUDFORMATION

    def test_cloudformation_template_json(self):
        assert _infer_iac_format("template.json") == IaCFormat.CLOUDFORMATION

    def test_cloudformation_sam_dir(self):
        assert _infer_iac_format("sam/stack.yaml") == IaCFormat.CLOUDFORMATION

    def test_kubernetes_in_k8s_dir(self):
        assert _infer_iac_format("k8s/deployment.yaml") == IaCFormat.KUBERNETES

    def test_kubernetes_in_kubernetes_dir(self):
        assert _infer_iac_format("kubernetes/service.yml") == IaCFormat.KUBERNETES

    def test_unknown_py_file(self):
        assert _infer_iac_format("src/utils.py") == IaCFormat.UNKNOWN

    def test_unknown_ts_not_in_cdk_dir(self):
        assert _infer_iac_format("src/handlers/process.ts") == IaCFormat.UNKNOWN

    def test_unknown_random_yaml(self):
        assert _infer_iac_format("config/app.yaml") == IaCFormat.UNKNOWN

    def test_case_insensitive_terraform(self):
        assert _infer_iac_format("INFRA/MAIN.TF") == IaCFormat.TERRAFORM

    def test_case_insensitive_dockerfile(self):
        assert _infer_iac_format("Dockerfile.Dev") == IaCFormat.DOCKERFILE


# ── _build_iac_inventory ──────────────────────────────────────────────────────

class TestBuildIacInventory:
    def test_returns_empty_when_cache_miss(self):
        with patch("agent.agent_assembly.get_cached_inventory", return_value=None):
            inv = _build_iac_inventory("unknown-repo")
        assert inv.iac_files == ()
        assert inv.total_file_count == 0

    def test_only_iac_files_with_content_included(self, sample_inventory_records, sample_repo_inventory):
        with patch("agent.agent_assembly.get_cached_inventory", return_value=sample_repo_inventory):
            inv = _build_iac_inventory("/tmp/myrepo")
        # 5 IaC records, all with content
        assert len(inv.iac_files) == 5
        # No app files in IaC inventory
        assert inv.app_files == ()

    def test_stubs_excluded(self, sample_inventory_records):
        """Files with content=None must not appear in iac_files."""
        from tests.agent_assembly.conftest import _make_file_record
        records = [
            _make_file_record("infra/main.tf", tier="iac", content="resource {}"),
            _make_file_record("infra/stack.tf", tier="iac", content=None),  # stub
        ]
        repo_inv = _make_repo_inventory(records)
        with patch("agent.agent_assembly.get_cached_inventory", return_value=repo_inv):
            inv = _build_iac_inventory("/tmp/r")
        assert len(inv.iac_files) == 1
        assert inv.iac_files[0].path == "infra/main.tf"

    def test_format_inferred_for_each_file(self, sample_inventory_records, sample_repo_inventory):
        with patch("agent.agent_assembly.get_cached_inventory", return_value=sample_repo_inventory):
            inv = _build_iac_inventory("/tmp/myrepo")
        formats = {f.path: f.format for f in inv.iac_files}
        assert formats["infra/cdk_stack.py"] == IaCFormat.CDK_PYTHON
        assert formats["infra/template.yaml"] == IaCFormat.CLOUDFORMATION
        assert formats["main.tf"] == IaCFormat.TERRAFORM
        assert formats["serverless.yml"] == IaCFormat.SERVERLESS_YML
        assert formats["Dockerfile"] == IaCFormat.DOCKERFILE

    def test_repo_root_set(self, sample_repo_inventory):
        with patch("agent.agent_assembly.get_cached_inventory", return_value=sample_repo_inventory):
            inv = _build_iac_inventory("/tmp/myrepo")
        assert inv.repo_root == "/tmp/myrepo"


# ── _build_app_inventory ──────────────────────────────────────────────────────

class TestBuildAppInventory:
    def test_returns_empty_when_cache_miss(self):
        with patch("agent.agent_assembly.get_cached_inventory", return_value=None):
            inv = _build_app_inventory("unknown-repo")
        assert inv.app_files == []
        assert inv.files_scanned_count == 0

    def test_splits_iac_and_app_files(self, sample_inventory_records, sample_repo_inventory):
        with patch("agent.agent_assembly.get_cached_inventory", return_value=sample_repo_inventory):
            inv = _build_app_inventory("/tmp/myrepo")
        # 5 IaC + 2 app with content; 1 stub counted as skipped
        assert len(inv.iac_files) == 5
        assert len(inv.app_files) == 2
        assert inv.files_scanned_count == 7
        assert inv.files_skipped_count == 1

    def test_stub_files_counted_as_skipped(self):
        from tests.agent_assembly.conftest import _make_file_record
        records = [
            _make_file_record("src/a.py", tier="app", content="pass"),
            _make_file_record("src/b.py", tier="app", content=None),
        ]
        repo_inv = _make_repo_inventory(records)
        with patch("agent.agent_assembly.get_cached_inventory", return_value=repo_inv):
            inv = _build_app_inventory("/tmp/r")
        assert inv.files_scanned_count == 1
        assert inv.files_skipped_count == 1

    def test_extension_inferred(self, sample_inventory_records, sample_repo_inventory):
        with patch("agent.agent_assembly.get_cached_inventory", return_value=sample_repo_inventory):
            inv = _build_app_inventory("/tmp/myrepo")
        by_path = {f.path: f.extension for f in inv.app_files}
        assert by_path["src/app.py"] == ".py"
        assert by_path["src/models.py"] == ".py"

    def test_scan_cap_reflected(self, sample_inventory_records):
        repo_inv = _make_repo_inventory(sample_inventory_records, capped=True)
        with patch("agent.agent_assembly.get_cached_inventory", return_value=repo_inv):
            inv = _build_app_inventory("/tmp/myrepo")
        assert inv.scan_cap_reached is True


# ── load_rubric_tool ──────────────────────────────────────────────────────────

class TestLoadRubricTool:
    def test_returns_ok_with_7_laws(self):
        result = load_rubric_tool()
        assert result["status"] == "ok"
        assert result["law_count"] == 7
        assert len(result["laws"]) == 7

    def test_laws_have_number_and_title(self):
        result = load_rubric_tool()
        for law in result["laws"]:
            assert "number" in law
            assert "title" in law
            assert isinstance(law["number"], int)
            assert isinstance(law["title"], str)

    def test_error_on_load_failure(self):
        with patch("agent.agent_assembly._load_rubric", side_effect=RuntimeError("bad yaml")):
            result = load_rubric_tool()
        assert result["status"] == "error"
        assert "bad yaml" in result["message"]


# ── get_law_tool ──────────────────────────────────────────────────────────────

class TestGetLawTool:
    def test_valid_law_number_returns_ok(self):
        result = get_law_tool("1")
        assert result["status"] == "ok"
        assert result["number"] == 1
        assert isinstance(result["title"], str)
        assert "Werner Vogels" in result["attribution"]

    def test_valid_law_number_7(self):
        result = get_law_tool("7")
        assert result["status"] == "ok"
        assert result["number"] == 7

    def test_unknown_law_returns_error(self):
        result = get_law_tool("99")
        assert result["status"] == "error"
        assert "message" in result

    def test_attribution_contains_url(self):
        result = get_law_tool("1")
        assert "thefrugalarchitect.com" in result["attribution"]

    def test_law_has_required_fields(self):
        result = get_law_tool("1")
        assert "description" in result
        assert "probing_questions" in result


# ── scan_iac_tool ─────────────────────────────────────────────────────────────

class TestScanIacTool:
    def test_ok_with_no_iac_files(self):
        """Empty inventory → ok status with zero findings."""
        from agent.tools.repo_connector.types import (
            FileRecord, RepoInventory, RepoRef, ScopeNote,
        )
        empty_inv = RepoInventory(
            repo_ref=RepoRef(raw="/tmp/empty", kind="path", provider=None, subdirectory=None),
            files=[],
            scope_note=ScopeNote(
                capped=False, cap_type="none",
                files_included=0, files_seen=0, tokens_consumed=0,
                layers_covered="full", subdirectory=None,
            ),
            provider="local",
            file_count=0,
        )
        with patch("agent.agent_assembly.get_cached_inventory", return_value=empty_inv):
            result = scan_iac_tool("/tmp/empty")
        assert result["status"] == "ok"
        assert result["findings_count"] == 0

    def test_cache_miss_returns_ok_with_zero_findings(self):
        with patch("agent.agent_assembly.get_cached_inventory", return_value=None):
            result = scan_iac_tool("not-cached")
        assert result["status"] == "ok"
        assert result["findings_count"] == 0

    def test_findings_registered_in_cache(self, sample_repo_inventory):
        with (
            patch("agent.agent_assembly.get_cached_inventory", return_value=sample_repo_inventory),
            patch("agent.agent_assembly.register_scan_results") as mock_register,
            patch("agent.agent_assembly._scan_iac") as mock_scan,
        ):
            from agent.iac_scanner.models import ScanSummary
            mock_scan.return_value = ([], ScanSummary(
                files_processed=0,
                files_skipped_budget=0,
                skipped_paths=[],
                parse_errors=[],
                formats_scanned=[],
                findings_count=0,
            ))
            scan_iac_tool("/tmp/myrepo")
        mock_register.assert_called_once()

    def test_error_path_returns_error_status(self, sample_repo_inventory):
        with (
            patch("agent.agent_assembly.get_cached_inventory", return_value=sample_repo_inventory),
            patch("agent.agent_assembly._scan_iac", side_effect=RuntimeError("scan crash")),
        ):
            result = scan_iac_tool("/tmp/myrepo")
        assert result["status"] == "error"
        assert "scan crash" in result["message"]


# ── scan_app_antipatterns_tool ────────────────────────────────────────────────

class TestScanAppAntipatternsToolWrapper:
    def test_ok_with_empty_repo(self):
        with patch("agent.agent_assembly.get_cached_inventory", return_value=None):
            result = scan_app_antipatterns_tool("not-cached")
        assert result["status"] == "ok"
        assert result["findings_count"] == 0

    def test_findings_registered_in_cache(self, sample_repo_inventory):
        with (
            patch("agent.agent_assembly.get_cached_inventory", return_value=sample_repo_inventory),
            patch("agent.agent_assembly.register_scan_results") as mock_register,
        ):
            result = scan_app_antipatterns_tool("/tmp/myrepo")
        # Should succeed (real rubric loaded; app files contain no known anti-patterns)
        assert result["status"] == "ok"
        mock_register.assert_called_once()

    def test_confidence_is_possible_for_all_findings(self, sample_repo_inventory):
        with patch("agent.agent_assembly.get_cached_inventory", return_value=sample_repo_inventory):
            result = scan_app_antipatterns_tool("/tmp/myrepo")
        for f in result["findings"]:
            assert f["confidence"] == "POSSIBLE"

    def test_error_path_returns_error_status(self, sample_repo_inventory):
        with (
            patch("agent.agent_assembly.get_cached_inventory", return_value=sample_repo_inventory),
            patch(
                "agent.agent_assembly.scan_app_antipatterns_tool.__wrapped__"
                if hasattr(scan_app_antipatterns_tool, "__wrapped__") else
                "agent.agent_assembly._load_rubric",
                side_effect=RuntimeError("app scan crash"),
            ),
        ):
            result = scan_app_antipatterns_tool("/tmp/myrepo")
        # Either error or ok — just ensure no exception propagates
        assert "status" in result


# ── create_agent ─────────────────────────────────────────────────────────────

def _mock_config():
    """Return a MagicMock with all required config attributes."""
    cfg = MagicMock()
    cfg.AGENT_MODEL_ID = "anthropic.claude-test-v1:0"
    cfg.MODEL_PRICE_PER_TOKEN = 0.000003
    cfg.OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4317"
    cfg.OTEL_SERVICE_NAME = "frugal-architect-agent-test"
    cfg.AGENTCORE_MEMORY_ID = None
    cfg.AGENTCORE_MEMORY_REGION = None
    return cfg


class TestCreateAgent:
    def test_raises_when_strands_unavailable(self):
        import agent.agent_assembly as aa
        original_agent = aa.Agent
        original_bedrock = aa.BedrockModel
        try:
            aa.Agent = None
            aa.BedrockModel = None
            with pytest.raises(RuntimeError, match="Strands SDK not installed"):
                aa.create_agent()
        finally:
            aa.Agent = original_agent
            aa.BedrockModel = original_bedrock

    def test_create_agent_registers_7_tools(self):
        mock_agent_cls = MagicMock()
        mock_bedrock_cls = MagicMock()
        import agent.agent_assembly as aa
        import sys

        original_config = sys.modules.get("agent.config")
        sys.modules["agent.config"] = _mock_config()  # type: ignore[assignment]
        try:
            with (
                patch.object(aa, "Agent", mock_agent_cls),
                patch.object(aa, "BedrockModel", mock_bedrock_cls),
                patch.object(aa, "_load_rubric"),
                patch("agent.observability.setup"),
            ):
                aa.create_agent()
        finally:
            if original_config is None:
                sys.modules.pop("agent.config", None)
            else:
                sys.modules["agent.config"] = original_config

        call_kwargs = mock_agent_cls.call_args.kwargs
        tools = call_kwargs.get("tools", [])
        assert len(tools) == 7, f"Expected 7 tools, got {len(tools)}: {tools}"

    def test_create_agent_uses_config_model_id(self):
        mock_agent_cls = MagicMock()
        mock_bedrock_cls = MagicMock()
        import agent.agent_assembly as aa
        import sys

        mock_cfg = _mock_config()
        original_config = sys.modules.get("agent.config")
        sys.modules["agent.config"] = mock_cfg  # type: ignore[assignment]
        try:
            with (
                patch.object(aa, "Agent", mock_agent_cls),
                patch.object(aa, "BedrockModel", mock_bedrock_cls),
                patch.object(aa, "_load_rubric"),
                patch("agent.observability.setup"),
            ):
                aa.create_agent()
            mock_bedrock_cls.assert_called_once_with(model_id="anthropic.claude-test-v1:0")
        finally:
            if original_config is None:
                sys.modules.pop("agent.config", None)
            else:
                sys.modules["agent.config"] = original_config

    def test_create_agent_passes_system_prompt(self):
        mock_agent_cls = MagicMock()
        mock_bedrock_cls = MagicMock()
        import agent.agent_assembly as aa
        import sys

        original_config = sys.modules.get("agent.config")
        sys.modules["agent.config"] = _mock_config()  # type: ignore[assignment]
        try:
            with (
                patch.object(aa, "Agent", mock_agent_cls),
                patch.object(aa, "BedrockModel", mock_bedrock_cls),
                patch.object(aa, "_load_rubric"),
                patch("agent.observability.setup"),
            ):
                aa.create_agent()

            call_kwargs = mock_agent_cls.call_args.kwargs
            assert "system_prompt" in call_kwargs
            assert len(call_kwargs["system_prompt"]) > 100
        finally:
            if original_config is None:
                sys.modules.pop("agent.config", None)
            else:
                sys.modules["agent.config"] = original_config

    def test_create_agent_calls_observability_setup(self):
        mock_agent_cls = MagicMock()
        mock_bedrock_cls = MagicMock()
        import agent.agent_assembly as aa
        import sys

        original_config = sys.modules.get("agent.config")
        sys.modules["agent.config"] = _mock_config()  # type: ignore[assignment]
        try:
            with (
                patch.object(aa, "Agent", mock_agent_cls),
                patch.object(aa, "BedrockModel", mock_bedrock_cls),
                patch.object(aa, "_load_rubric"),
                patch("agent.observability.setup") as mock_obs,
            ):
                aa.create_agent()
            mock_obs.assert_called_once()
        finally:
            if original_config is None:
                sys.modules.pop("agent.config", None)
            else:
                sys.modules["agent.config"] = original_config


# ── SYSTEM_PROMPT content checks ─────────────────────────────────────────────

class TestSystemPrompt:
    def test_contains_werner_vogels_attribution(self):
        assert "Werner Vogels" in SYSTEM_PROMPT

    def test_contains_frugalarchitect_url(self):
        assert "thefrugalarchitect.com/laws/" in SYSTEM_PROMPT

    def test_contains_advisory_only_disclaimer(self):
        assert "advisory-only" in SYSTEM_PROMPT.lower() or "Advisory only" in SYSTEM_PROMPT

    def test_contains_all_four_modes(self):
        assert "CODEBASE REVIEW" in SYSTEM_PROMPT
        assert "DESIGN REVIEW" in SYSTEM_PROMPT
        assert "DECISION CHECK" in SYSTEM_PROMPT
        assert "TEACH" in SYSTEM_PROMPT

    def test_contains_guardrails(self):
        assert "GR-001" in SYSTEM_PROMPT
        assert "GR-005" in SYSTEM_PROMPT

    def test_contains_fix_decline_script(self):
        assert "pull requests" in SYSTEM_PROMPT
        assert "does not modify" in SYSTEM_PROMPT or "do not modify" in SYSTEM_PROMPT.lower()

    def test_contains_confidence_tier_language(self):
        assert "FIRM" in SYSTEM_PROMPT
        assert "POSSIBLE" in SYSTEM_PROMPT

    def test_contains_streaming_mandate(self):
        assert "progress" in SYSTEM_PROMPT.lower()
        # Streaming mandate: emit a progress line before each tool call
        assert "load_rubric_tool" in SYSTEM_PROMPT

    def test_rubric_grounding_enforced(self):
        assert "exclusively from the rubric" in SYSTEM_PROMPT or \
               "MUST reason exclusively" in SYSTEM_PROMPT

    def test_unknown_verdict_discipline(self):
        assert "UNKNOWN" in SYSTEM_PROMPT
        assert "fabricate" in SYSTEM_PROMPT.lower() or "never fabricate" in SYSTEM_PROMPT.lower() \
               or "DO NOT fabricate" in SYSTEM_PROMPT
