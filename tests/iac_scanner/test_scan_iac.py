"""
Tests for scan_iac engine — covers BR-01 through BR-11 and the 7-law rubric signals.
"""

from __future__ import annotations

import os

import pytest
import yaml

from agent.iac_scanner.engine import scan_iac
from agent.iac_scanner.models import (
    Confidence,
    File,
    FirmFinding,
    IaCFormat,
    PrioritizedInventory,
    ScanSummary,
)
from agent.rubric_core.engine import load_rubric

from tests.iac_scanner.conftest import (
    cf_lambda,
    cf_template,
    make_file,
    make_inventory,
    make_iac_signal,
    make_rubric_with_signals,
)


# ── BR-01: No fabrication on empty inventory ──────────────────────────────────

class TestEmptyInventory:
    def test_empty_inventory_returns_no_findings(self) -> None:
        rubric = make_rubric_with_signals()
        findings, summary = scan_iac(PrioritizedInventory.empty(), rubric)
        assert findings == []

    def test_empty_inventory_returns_valid_summary(self) -> None:
        rubric = make_rubric_with_signals()
        _, summary = scan_iac(PrioritizedInventory.empty(), rubric)
        assert summary.findings_count == 0
        assert summary.files_processed == 0

    def test_no_signals_no_findings(self) -> None:
        """No IaCSignals in rubric → no findings even with files present."""
        rubric = make_rubric_with_signals()  # all laws have empty iac_signals
        template = cf_template({"MyFunc": cf_lambda("MyFunc", memory=2048)})
        inv = make_inventory(make_file("template.yaml", template))
        findings, _ = scan_iac(inv, rubric)
        assert findings == []


# ── BR-03: FIRM confidence only ───────────────────────────────────────────────

class TestFirmConfidenceOnly:
    def test_all_findings_are_firm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        signal = make_iac_signal(
            id="mem-signal",
            formats=["cloudformation"],
            resource_type_filter="AWS::Lambda::Function",
            attribute_path="Properties.MemorySize",
            predicate="> 512",
        )
        rubric = make_rubric_with_signals({1: [signal]})
        template = cf_template({"MyFunc": cf_lambda("MyFunc", memory=1024)})
        inv = make_inventory(make_file("t.yaml", template))
        findings, _ = scan_iac(inv, rubric)
        assert all(f.confidence == Confidence.FIRM for f in findings)


# ── BR-04: Mandatory law citation ─────────────────────────────────────────────

class TestLawCitation:
    def test_every_finding_has_law_id_and_title(self) -> None:
        signal = make_iac_signal(predicate="> 512")
        rubric = make_rubric_with_signals({1: [signal]})
        template = cf_template({"MyFunc": cf_lambda("MyFunc", memory=1024)})
        inv = make_inventory(make_file("t.yaml", template))
        findings, _ = scan_iac(inv, rubric)
        for f in findings:
            assert f.law_id.startswith("law-")
            assert f.law_title


# ── BR-05: Minimum evidence fields ───────────────────────────────────────────

class TestMinimumEvidenceFields:
    def test_every_finding_has_non_empty_file_path(self) -> None:
        signal = make_iac_signal(predicate="> 512")
        rubric = make_rubric_with_signals({1: [signal]})
        template = cf_template({"MyFunc": cf_lambda("MyFunc", memory=1024)})
        inv = make_inventory(make_file("infra/stack.yaml", template))
        findings, _ = scan_iac(inv, rubric)
        assert all(f.file_path for f in findings)

    def test_every_finding_has_non_empty_resource_id(self) -> None:
        signal = make_iac_signal(predicate="> 512")
        rubric = make_rubric_with_signals({1: [signal]})
        template = cf_template({"MyFunction": cf_lambda("MyFunction", memory=1024)})
        inv = make_inventory(make_file("t.yaml", template))
        findings, _ = scan_iac(inv, rubric)
        assert all(f.resource_id for f in findings)

    def test_firm_finding_rejects_empty_file_path(self) -> None:
        with pytest.raises(ValueError, match="file_path"):
            FirmFinding(
                law_id="law-1", law_title="Test", file_path="",
                resource_id="res", resource_type="t", line=None,
                gap="gap", evidence="ev", signal_id="s",
            )

    def test_firm_finding_rejects_empty_resource_id(self) -> None:
        with pytest.raises(ValueError, match="resource_id"):
            FirmFinding(
                law_id="law-1", law_title="Test", file_path="t.yaml",
                resource_id="", resource_type="t", line=None,
                gap="gap", evidence="ev", signal_id="s",
            )


# ── BR-06: Parse error isolation ─────────────────────────────────────────────

class TestParseErrorIsolation:
    def test_malformed_yaml_does_not_abort_other_files(self) -> None:
        signal = make_iac_signal(predicate="> 512")
        rubric = make_rubric_with_signals({1: [signal]})
        good_template = cf_template({"MyFunc": cf_lambda("MyFunc", memory=1024)})
        bad_content = "key: [\nunclosed"
        inv = make_inventory(
            make_file("bad.yaml", bad_content),
            make_file("good.yaml", good_template),
        )
        findings, summary = scan_iac(inv, rubric)
        # Good file should produce a finding
        assert len(findings) >= 1
        # Error recorded
        assert len(summary.parse_errors) >= 1

    def test_parse_errors_recorded_in_summary(self) -> None:
        rubric = make_rubric_with_signals()
        inv = make_inventory(make_file("bad.yaml", "key: [\nunclosed"))
        _, summary = scan_iac(inv, rubric)
        assert summary.parse_errors  # at least one error


# ── BR-07: Deduplication ──────────────────────────────────────────────────────

class TestDeduplication:
    def test_same_file_resource_law_deduplicated(self) -> None:
        """Two signals on the same law+resource in one file → single finding."""
        signal_a = make_iac_signal(id="sig-a", predicate="> 512", severity_rank=1)
        signal_b = make_iac_signal(id="sig-b", predicate="> 100", severity_rank=2)
        rubric = make_rubric_with_signals({1: [signal_a, signal_b]})
        template = cf_template({"MyFunc": cf_lambda("MyFunc", memory=1024)})
        inv = make_inventory(make_file("t.yaml", template))
        findings, _ = scan_iac(inv, rubric)
        # Both signals triggered but for same (file, resource, law) → 1 finding
        # Note: signal_a and signal_b have different signal_ids but same (file, resource, law)
        keys = [(f.file_path, f.resource_id, f.law_id) for f in findings]
        assert len(keys) == len(set(keys))  # no duplicates


# ── BR-08: Scan budget enforcement ───────────────────────────────────────────

class TestScanBudget:
    def test_skipped_paths_recorded_when_budget_exceeded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Set a tiny budget (1 token) so the second file is skipped
        monkeypatch.setenv("SCAN_TOKEN_BUDGET", "1")
        rubric = make_rubric_with_signals()
        file1 = make_file("a.yaml", cf_template({"R": cf_lambda("R")}))
        file2 = make_file("b.yaml", cf_template({"R2": cf_lambda("R2")}))
        inv = make_inventory(file1, file2)
        _, summary = scan_iac(inv, rubric)
        assert summary.files_skipped_budget > 0
        assert summary.skipped_paths


# ── Rubric integrity ──────────────────────────────────────────────────────────

class TestRubricIntegrity:
    def test_rubric_with_wrong_law_count_raises(self) -> None:
        from agent.rubric_core.models import LawDefinition, Rubric, AntiPatternSignal
        # Bypass the Rubric model_validator by building with 6 laws (would fail)
        # Instead test the scan_iac guard directly
        signal = make_iac_signal()
        rubric = make_rubric_with_signals({1: [signal]})
        # Manually patch laws to have 6
        bad_laws = rubric.laws[:6]
        bad_rubric = Rubric.model_construct(
            version=rubric.version,
            attribution=rubric.attribution,
            laws=bad_laws,
            anti_pattern_signals=rubric.anti_pattern_signals,
        )
        inv = make_inventory(make_file("t.yaml", cf_template({"R": cf_lambda("R")})))
        with pytest.raises(ValueError, match="7"):
            scan_iac(inv, bad_rubric)


# ── Scan summary correctness ──────────────────────────────────────────────────

class TestScanSummary:
    def test_summary_counts_processed_files(self) -> None:
        rubric = make_rubric_with_signals()
        inv = make_inventory(
            make_file("a.yaml", cf_template({"R1": cf_lambda("R1")})),
            make_file("b.yaml", cf_template({"R2": cf_lambda("R2")})),
        )
        _, summary = scan_iac(inv, rubric)
        assert summary.files_processed == 2

    def test_summary_findings_count_matches_list_length(self) -> None:
        signal = make_iac_signal(predicate="> 512")
        rubric = make_rubric_with_signals({1: [signal]})
        inv = make_inventory(make_file("t.yaml", cf_template({"F": cf_lambda("F", memory=1024)})))
        findings, summary = scan_iac(inv, rubric)
        assert summary.findings_count == len(findings)

    def test_formats_scanned_populated(self) -> None:
        rubric = make_rubric_with_signals()
        inv = make_inventory(make_file("t.yaml", cf_template({"R": cf_lambda("R")})))
        _, summary = scan_iac(inv, rubric)
        assert "cloudformation" in summary.formats_scanned


# ── CloudFormation-specific signal detection ──────────────────────────────────

class TestCloudFormationSignals:
    def test_lambda_over_memory_detected(self) -> None:
        signal = make_iac_signal(
            id="cf-mem",
            formats=["cloudformation"],
            resource_type_filter="AWS::Lambda::Function",
            attribute_path="Properties.MemorySize",
            predicate="> 1024",
        )
        rubric = make_rubric_with_signals({1: [signal]})
        template = cf_template({"BigLambda": cf_lambda("BigLambda", memory=2048)})
        inv = make_inventory(make_file("t.yaml", template))
        findings, _ = scan_iac(inv, rubric)
        assert len(findings) == 1
        assert findings[0].resource_id == "BigLambda"
        assert findings[0].law_id == "law-1"

    def test_lambda_under_memory_not_detected(self) -> None:
        signal = make_iac_signal(
            id="cf-mem",
            formats=["cloudformation"],
            resource_type_filter="AWS::Lambda::Function",
            attribute_path="Properties.MemorySize",
            predicate="> 1024",
        )
        rubric = make_rubric_with_signals({1: [signal]})
        template = cf_template({"SmallLambda": cf_lambda("SmallLambda", memory=256)})
        inv = make_inventory(make_file("t.yaml", template))
        findings, _ = scan_iac(inv, rubric)
        assert len(findings) == 0

    def test_lambda_no_tracing_detected(self) -> None:
        signal = make_iac_signal(
            id="cf-tracing",
            formats=["cloudformation"],
            resource_type_filter="AWS::Lambda::Function",
            attribute_path="Properties.TracingConfig.Mode",
            predicate="!= Active",
        )
        rubric = make_rubric_with_signals({4: [signal]})
        # Lambda without TracingConfig → attribute absent → None != Active → True
        template = cf_template({"NoTrace": cf_lambda("NoTrace")})
        inv = make_inventory(make_file("t.yaml", template))
        findings, _ = scan_iac(inv, rubric)
        assert len(findings) == 1
        assert findings[0].law_id == "law-4"

    def test_lambda_with_active_tracing_not_flagged(self) -> None:
        signal = make_iac_signal(
            id="cf-tracing",
            formats=["cloudformation"],
            resource_type_filter="AWS::Lambda::Function",
            attribute_path="Properties.TracingConfig.Mode",
            predicate="!= Active",
        )
        rubric = make_rubric_with_signals({4: [signal]})
        template = cf_template({"TracedFunc": cf_lambda("TracedFunc", tracing="Active")})
        inv = make_inventory(make_file("t.yaml", template))
        findings, _ = scan_iac(inv, rubric)
        assert len(findings) == 0

    def test_lambda_no_reserved_concurrency_detected(self) -> None:
        signal = make_iac_signal(
            id="cf-concurrency",
            formats=["cloudformation"],
            resource_type_filter="AWS::Lambda::Function",
            attribute_path="Properties.ReservedConcurrentExecutions",
            predicate="== null",
        )
        rubric = make_rubric_with_signals({5: [signal]})
        template = cf_template({"Unbounded": cf_lambda("Unbounded")})
        inv = make_inventory(make_file("t.yaml", template))
        findings, _ = scan_iac(inv, rubric)
        assert len(findings) == 1

    def test_lambda_with_reserved_concurrency_not_flagged(self) -> None:
        signal = make_iac_signal(
            id="cf-concurrency",
            formats=["cloudformation"],
            resource_type_filter="AWS::Lambda::Function",
            attribute_path="Properties.ReservedConcurrentExecutions",
            predicate="== null",
        )
        rubric = make_rubric_with_signals({5: [signal]})
        template = cf_template({"Bounded": cf_lambda("Bounded", reserved=10)})
        inv = make_inventory(make_file("t.yaml", template))
        findings, _ = scan_iac(inv, rubric)
        assert len(findings) == 0

    def test_resource_type_filter_excludes_other_types(self) -> None:
        """Signal for AWS::Lambda::Function should not match other resource types."""
        signal = make_iac_signal(
            id="cf-mem",
            formats=["cloudformation"],
            resource_type_filter="AWS::Lambda::Function",
            attribute_path="Properties.MemorySize",
            predicate="> 512",
        )
        rubric = make_rubric_with_signals({1: [signal]})
        template = cf_template({
            "MyBucket": {
                "Type": "AWS::S3::Bucket",
                "Properties": {"MemorySize": 9999},
            }
        })
        inv = make_inventory(make_file("t.yaml", template))
        findings, _ = scan_iac(inv, rubric)
        assert len(findings) == 0


# ── Terraform-specific signal detection ───────────────────────────────────────

class TestTerraformSignals:
    TF_LAMBDA_CONTENT = """
resource "aws_lambda_function" "my_func" {
  function_name = "my-function"
  memory_size   = 2048
  runtime       = "python3.12"
  role          = "arn:aws:iam::123456789012:role/lambda-role"
  handler       = "index.handler"
  filename      = "lambda.zip"
}
"""

    def test_lambda_over_memory_detected(self) -> None:
        signal = make_iac_signal(
            id="tf-mem",
            formats=["terraform"],
            resource_type_filter="aws_lambda_function",
            attribute_path="memory_size",
            predicate="> 1024",
        )
        rubric = make_rubric_with_signals({1: [signal]})
        inv = make_inventory(make_file("main.tf", self.TF_LAMBDA_CONTENT, IaCFormat.TERRAFORM))
        findings, _ = scan_iac(inv, rubric)
        assert len(findings) == 1
        assert findings[0].resource_id == "aws_lambda_function.my_func"

    def test_lambda_no_tracing_detected(self) -> None:
        signal = make_iac_signal(
            id="tf-tracing",
            formats=["terraform"],
            resource_type_filter="aws_lambda_function",
            attribute_path="tracing_config.mode",
            predicate="!= Active",
        )
        rubric = make_rubric_with_signals({4: [signal]})
        # No tracing_config block → attribute absent → flagged
        inv = make_inventory(make_file("main.tf", self.TF_LAMBDA_CONTENT, IaCFormat.TERRAFORM))
        findings, _ = scan_iac(inv, rubric)
        assert len(findings) == 1

    TF_LAMBDA_WITH_TRACING = """
resource "aws_lambda_function" "traced" {
  function_name = "traced-function"
  memory_size   = 128
  tracing_config {
    mode = "Active"
  }
}
"""

    def test_lambda_with_active_tracing_not_flagged(self) -> None:
        signal = make_iac_signal(
            id="tf-tracing",
            formats=["terraform"],
            resource_type_filter="aws_lambda_function",
            attribute_path="tracing_config.mode",
            predicate="!= Active",
        )
        rubric = make_rubric_with_signals({4: [signal]})
        inv = make_inventory(
            make_file("main.tf", self.TF_LAMBDA_WITH_TRACING, IaCFormat.TERRAFORM)
        )
        findings, _ = scan_iac(inv, rubric)
        assert len(findings) == 0


# ── Serverless-specific signal detection ─────────────────────────────────────

class TestServerlessSignals:
    SLS_CONTENT = """
service: my-service
provider:
  name: aws
  runtime: python3.12
functions:
  hello:
    handler: handler.hello
    memorySize: 2048
  world:
    handler: handler.world
    memorySize: 128
"""

    def test_over_memory_function_detected(self) -> None:
        signal = make_iac_signal(
            id="sls-mem",
            formats=["serverless"],
            attribute_path="memorySize",
            predicate="> 1024",
        )
        rubric = make_rubric_with_signals({1: [signal]})
        inv = make_inventory(
            make_file("serverless.yml", self.SLS_CONTENT, IaCFormat.SERVERLESS_YML)
        )
        findings, _ = scan_iac(inv, rubric)
        # Only 'hello' exceeds 1024
        assert len(findings) == 1
        assert findings[0].resource_id == "hello"

    def test_functions_without_tracing_detected(self) -> None:
        signal = make_iac_signal(
            id="sls-tracing",
            formats=["serverless"],
            attribute_path="tracing",
            predicate="!= Active",
        )
        rubric = make_rubric_with_signals({4: [signal]})
        inv = make_inventory(
            make_file("serverless.yml", self.SLS_CONTENT, IaCFormat.SERVERLESS_YML)
        )
        findings, _ = scan_iac(inv, rubric)
        # Both functions lack tracing
        assert len(findings) == 2


# ── Kubernetes-specific signal detection ─────────────────────────────────────

class TestKubernetesSignals:
    K8S_DEPLOYMENT_NO_LIMITS = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
spec:
  replicas: 3
  template:
    spec:
      containers:
        - name: app
          image: my-image:latest
"""

    K8S_DEPLOYMENT_WITH_LIMITS = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bounded-app
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: app
          image: my-image:latest
          resources:
            limits:
              cpu: "500m"
              memory: "256Mi"
"""

    def test_deployment_without_resource_limits_detected(self) -> None:
        signal = make_iac_signal(
            id="k8s-limits",
            formats=["kubernetes"],
            resource_type_filter="Deployment",
            attribute_path="spec.template.spec.containers.0.resources.limits",
            predicate="== null",
        )
        rubric = make_rubric_with_signals({7: [signal]})
        inv = make_inventory(
            make_file("deploy.yaml", self.K8S_DEPLOYMENT_NO_LIMITS, IaCFormat.KUBERNETES)
        )
        findings, _ = scan_iac(inv, rubric)
        assert len(findings) == 1
        assert findings[0].resource_id == "my-app"

    def test_deployment_with_resource_limits_not_flagged(self) -> None:
        signal = make_iac_signal(
            id="k8s-limits",
            formats=["kubernetes"],
            resource_type_filter="Deployment",
            attribute_path="spec.template.spec.containers.0.resources.limits",
            predicate="== null",
        )
        rubric = make_rubric_with_signals({7: [signal]})
        inv = make_inventory(
            make_file("deploy.yaml", self.K8S_DEPLOYMENT_WITH_LIMITS, IaCFormat.KUBERNETES)
        )
        findings, _ = scan_iac(inv, rubric)
        assert len(findings) == 0


# ── Sort order ────────────────────────────────────────────────────────────────

class TestSortOrder:
    def test_findings_sorted_by_law_id(self) -> None:
        sig_law1 = make_iac_signal(id="s1", predicate="> 512")
        sig_law4 = make_iac_signal(
            id="s4", attribute_path="Properties.TracingConfig.Mode", predicate="!= Active"
        )
        rubric = make_rubric_with_signals({1: [sig_law1], 4: [sig_law4]})
        template = cf_template({"Func": cf_lambda("Func", memory=1024)})
        inv = make_inventory(make_file("t.yaml", template))
        findings, _ = scan_iac(inv, rubric)
        law_ids = [f.law_id for f in findings]
        assert law_ids == sorted(law_ids)


# ── Integration with real rubric ──────────────────────────────────────────────

class TestRealRubricIntegration:
    def test_real_rubric_loads_iac_signals(self) -> None:
        rubric = load_rubric()
        total_signals = sum(len(law.iac_signals) for law in rubric.laws)
        assert total_signals > 0, "Bundled rubric should have at least one IaC signal"

    def test_real_rubric_cf_lambda_over_memory(self) -> None:
        rubric = load_rubric()
        template = cf_template({"BigFunc": cf_lambda("BigFunc", memory=2048)})
        inv = make_inventory(make_file("template.yaml", template))
        findings, summary = scan_iac(inv, rubric)
        sig_ids = [f.signal_id for f in findings]
        assert "cf-lambda-over-memory" in sig_ids

    def test_real_rubric_cf_lambda_no_tracing(self) -> None:
        rubric = load_rubric()
        template = cf_template({"NoTrace": cf_lambda("NoTrace")})
        inv = make_inventory(make_file("template.yaml", template))
        findings, _ = scan_iac(inv, rubric)
        sig_ids = [f.signal_id for f in findings]
        assert "cf-lambda-no-xray" in sig_ids

    def test_real_rubric_cf_lambda_fully_compliant_no_findings(self) -> None:
        """A Lambda with good config should produce no findings for those signals."""
        rubric = load_rubric()
        # Memory <= 1024, active tracing, reserved concurrency, architectures set
        props = {
            "FunctionName": "GoodLambda",
            "Handler": "index.handler",
            "Runtime": "python3.12",
            "Role": "arn:aws:iam::123456789012:role/r",
            "MemorySize": 512,
            "TracingConfig": {"Mode": "Active"},
            "ReservedConcurrentExecutions": 100,
            "Architectures": ["arm64"],
        }
        template = cf_template({"GoodLambda": {"Type": "AWS::Lambda::Function", "Properties": props}})
        inv = make_inventory(make_file("template.yaml", template))
        findings, _ = scan_iac(inv, rubric)
        # These specific signals should not fire
        bad_ids = {"cf-lambda-over-memory", "cf-lambda-no-xray",
                   "cf-lambda-no-reserved-concurrency", "cf-lambda-default-architecture"}
        fired = {f.signal_id for f in findings}
        assert not (fired & bad_ids), f"Unexpected signals fired: {fired & bad_ids}"
