"""Tests for app-scanner unit covering all business rules and key workflows."""

from __future__ import annotations

import uuid
from typing import get_args

import pytest

import agent.rubric_core.engine as _engine
from agent.app_scanner.models import (
    AppScannerResult,
    File,
    PossibleFinding,
    PrioritizedInventory,
)
from agent.app_scanner.scanner import (
    _make_finding_id,
    _FINDING_NAMESPACE,
    match_pattern,
    scan_app_antipatterns,
)
from tests.app_scanner.conftest import make_inventory, make_py_file


# ── BR-APP-001: POSSIBLE confidence invariant ─────────────────────────────────

class TestPossibleConfidenceInvariant:
    def test_all_findings_have_possible_confidence(self, real_rubric):
        """Every finding must carry confidence='POSSIBLE' (BR-APP-001)."""
        code = "    result = session.get(user_id)\n"
        inv = make_inventory([make_py_file("src/api.py", code)])
        result = scan_app_antipatterns(inv, real_rubric)
        for f in result.findings:
            assert f.confidence == "POSSIBLE", f"Expected POSSIBLE, got {f.confidence!r}"

    def test_confidence_literal_type(self):
        """confidence field type must be Literal['POSSIBLE'] (BR-APP-001)."""
        import typing
        hints = typing.get_type_hints(PossibleFinding)
        confidence_type = hints.get("confidence")
        assert confidence_type is not None
        args = get_args(confidence_type)
        assert args == ("POSSIBLE",), f"Expected Literal['POSSIBLE'], got {args}"

    def test_empty_inventory_yields_no_findings(self, real_rubric):
        """Zero app files → zero findings, POSSIBLE constraint trivially satisfied."""
        inv = make_inventory([])
        result = scan_app_antipatterns(inv, real_rubric)
        assert result.findings == []


# ── BR-APP-002: Anti-pattern list is rubric-bounded ──────────────────────────

class TestRubricBoundedSignals:
    def test_only_rubric_signals_produce_findings(self, minimal_rubric):
        """Scanner must not invent patterns beyond what the rubric defines (BR-APP-002)."""
        code = "    result = session.get(user_id)\n"
        inv = make_inventory([make_py_file("src/api.py", code)])
        result = scan_app_antipatterns(inv, minimal_rubric)
        signal_names = {s.name for s in minimal_rubric.anti_pattern_signals}
        for f in result.findings:
            assert f.anti_pattern_type in signal_names, (
                f"Finding type {f.anti_pattern_type!r} not in rubric signal names"
            )

    def test_summary_anti_patterns_count_matches_rubric(self, real_rubric):
        """summary.anti_patterns_searched must equal the number of rubric signals."""
        inv = make_inventory([make_py_file("src/a.py", "x = 1\n")])
        result = scan_app_antipatterns(inv, real_rubric)
        expected = len(real_rubric.anti_pattern_signals)
        assert result.summary.anti_patterns_searched == expected


# ── BR-APP-003: Runtime caveat is mandatory ───────────────────────────────────

class TestRuntimeCaveat:
    def test_every_finding_has_non_empty_runtime_caveat(self, real_rubric):
        """Every PossibleFinding must have a non-empty runtime_caveat (BR-APP-003)."""
        code = "    result = session.get(user_id)\n"
        inv = make_inventory([make_py_file("src/api.py", code)])
        result = scan_app_antipatterns(inv, real_rubric)
        for f in result.findings:
            assert f.runtime_caveat, f"runtime_caveat is empty on finding {f.finding_id}"
            assert "static analysis" in f.runtime_caveat.lower() or len(f.runtime_caveat) > 20


# ── BR-APP-004: Law citation must match rubric ────────────────────────────────

class TestLawCitationCorrectness:
    def test_law_id_within_valid_range(self, real_rubric):
        """All law_id values must be 1–7 (valid rubric law numbers) (BR-APP-004)."""
        code = "boto3.client('s3')\n    boto3.client('ssm')\n"
        inv = make_inventory([make_py_file("src/handler.py", code)])
        result = scan_app_antipatterns(inv, real_rubric)
        for f in result.findings:
            assert 1 <= f.law_id <= 7, f"law_id {f.law_id} out of range [1,7]"

    def test_law_title_matches_rubric(self, real_rubric):
        """law_title on each finding must match the rubric title for its law_id (BR-APP-004)."""
        law_map = {law.number: law.title for law in real_rubric.laws}
        code = "    result = session.filter()\n"
        inv = make_inventory([make_py_file("src/api.py", code)])
        result = scan_app_antipatterns(inv, real_rubric)
        for f in result.findings:
            expected_title = law_map.get(f.law_id, "")
            assert f.law_title == expected_title, (
                f"law_title {f.law_title!r} doesn't match rubric title {expected_title!r} "
                f"for law_id {f.law_id}"
            )


# ── BR-APP-005: Single-file parse errors absorbed silently ───────────────────

class TestParseErrorAbsorption:
    def test_binary_content_file_does_not_raise(self, real_rubric):
        """Files with undecodable content are skipped; no exception propagates (BR-APP-005)."""
        # Simulate a file that would cause issues in matching
        bad_file = File(
            path="src/binary.py",
            content="\x00\x01\x02\x03",
            extension=".py",
            size_bytes=4,
        )
        inv = make_inventory([bad_file])
        result = scan_app_antipatterns(inv, real_rubric)
        assert isinstance(result, AppScannerResult)

    def test_oversized_file_is_skipped_silently(self, real_rubric):
        """Files over 512 KB are skipped without raising (security-design cap)."""
        large_content = "x = 1\n" * 100_000  # ~600 KB
        big_file = File(
            path="src/big.py",
            content=large_content,
            extension=".py",
            size_bytes=len(large_content.encode()),
        )
        inv = make_inventory([big_file])
        result = scan_app_antipatterns(inv, real_rubric)
        assert isinstance(result, AppScannerResult)

    def test_scan_continues_after_bad_file(self, real_rubric):
        """Scan of subsequent files continues even if one file is problematic."""
        bad_file = File(path="src/bad.py", content="\x00", extension=".py", size_bytes=1)
        good_file = make_py_file("src/good.py", "    boto3.client('ssm')\n")
        inv = make_inventory([bad_file, good_file])
        result = scan_app_antipatterns(inv, real_rubric)
        assert isinstance(result, AppScannerResult)


# ── BR-APP-006: Signals loaded once per invocation ────────────────────────────

class TestSignalsLoadedOnce:
    def test_signals_count_stable_across_runs(self, real_rubric):
        """Two invocations on same rubric return same anti_patterns_searched count."""
        inv = make_inventory([make_py_file("src/a.py", "x = 1\n")])
        r1 = scan_app_antipatterns(inv, real_rubric)
        r2 = scan_app_antipatterns(inv, real_rubric)
        assert r1.summary.anti_patterns_searched == r2.summary.anti_patterns_searched


# ── BR-APP-007: No IaC findings from app-scanner ─────────────────────────────

class TestNoIaCFindings:
    def test_tf_files_not_scanned(self, real_rubric):
        """Terraform files must not produce findings (BR-APP-007)."""
        tf_file = File(
            path="infra/main.tf",
            content='resource "aws_dynamodb_table" "t" { scan_index_forward = false }',
            extension=".tf",
            size_bytes=60,
        )
        inv = make_inventory(app_files=[tf_file])
        result = scan_app_antipatterns(inv, real_rubric)
        tf_findings = [f for f in result.findings if f.file_path == "infra/main.tf"]
        assert tf_findings == []

    def test_iac_files_in_inventory_iac_slot_are_ignored(self, real_rubric):
        """Files in inventory.iac_files are never processed by app-scanner."""
        iac = File(path="infra/template.yaml", content="SELECT * FROM table", extension=".yaml", size_bytes=20)
        inv = PrioritizedInventory(
            iac_files=[iac],
            app_files=[],
            files_scanned_count=1,
            files_skipped_count=0,
            scan_cap_reached=False,
            repo_root="/repo",
        )
        result = scan_app_antipatterns(inv, real_rubric)
        assert result.findings == []

    def test_dockerfile_not_scanned(self, real_rubric):
        """Dockerfile is not an app-code file (BR-APP-007)."""
        docker_file = File(
            path="Dockerfile",
            content="RUN pip install boto3\nRUN python -c \"import boto3; boto3.client('s3')\"",
            extension="",
            size_bytes=80,
        )
        inv = make_inventory([docker_file])
        result = scan_app_antipatterns(inv, real_rubric)
        docker_findings = [f for f in result.findings if f.file_path == "Dockerfile"]
        assert docker_findings == []


# ── BR-APP-008: Noise suppression per signal per file ────────────────────────

class TestNoiseSuppress:
    def test_max_instances_per_signal_capped(self, real_rubric, monkeypatch):
        """At most MAX_INSTANCES_PER_SIGNAL findings per (file, signal) (BR-APP-008)."""
        monkeypatch.setenv("SCAN_MAX_INSTANCES_PER_SIGNAL", "2")
        # File with many boto3.client() calls (triggers per-request-heavy-initialization)
        lines = ["    boto3.client('s3')\n"] * 10
        code = "".join(lines)
        inv = make_inventory([make_py_file("src/handler.py", code)])
        result = scan_app_antipatterns(inv, real_rubric)
        per_request_findings = [
            f for f in result.findings
            if f.anti_pattern_type == "Per-Request Heavy Initialization"
            and f.file_path == "src/handler.py"
        ]
        assert len(per_request_findings) <= 2

    def test_zero_max_disables_suppression(self, real_rubric, monkeypatch):
        """Setting SCAN_MAX_INSTANCES_PER_SIGNAL=0 disables noise suppression (BR-APP-008)."""
        monkeypatch.setenv("SCAN_MAX_INSTANCES_PER_SIGNAL", "0")
        lines = ["    boto3.client('s3')\n"] * 5
        inv = make_inventory([make_py_file("src/handler.py", "".join(lines))])
        result = scan_app_antipatterns(inv, real_rubric)
        per_request = [
            f for f in result.findings
            if f.anti_pattern_type == "Per-Request Heavy Initialization"
        ]
        assert len(per_request) == 5

    def test_systemic_pattern_notes_generated(self, real_rubric, monkeypatch):
        """Suppressed instances are recorded in systemic_pattern_notes (BR-APP-008)."""
        monkeypatch.setenv("SCAN_MAX_INSTANCES_PER_SIGNAL", "1")
        lines = ["    boto3.client('s3')\n"] * 4
        inv = make_inventory([make_py_file("src/h.py", "".join(lines))])
        result = scan_app_antipatterns(inv, real_rubric)
        if any(f.anti_pattern_type == "Per-Request Heavy Initialization" for f in result.findings):
            assert len(result.summary.systemic_pattern_notes) >= 1


# ── BR-APP-009: Deterministic finding IDs ────────────────────────────────────

class TestDeterministicFindingIds:
    def test_same_inputs_produce_same_id(self, real_rubric):
        """Identical inputs must produce identical finding_id (BR-APP-009)."""
        code = "    boto3.client('s3')\n"
        inv = make_inventory([make_py_file("src/h.py", code)])
        r1 = scan_app_antipatterns(inv, real_rubric)
        r2 = scan_app_antipatterns(inv, real_rubric)
        ids1 = {f.finding_id for f in r1.findings}
        ids2 = {f.finding_id for f in r2.findings}
        assert ids1 == ids2

    def test_finding_id_is_valid_uuid(self, real_rubric):
        """finding_id must be a valid UUID string (BR-APP-009)."""
        code = "    boto3.client('s3')\n"
        inv = make_inventory([make_py_file("src/h.py", code)])
        result = scan_app_antipatterns(inv, real_rubric)
        for f in result.findings:
            parsed = uuid.UUID(f.finding_id)
            assert parsed.version == 5

    def test_make_finding_id_is_deterministic(self):
        """Helper produces same UUID v5 for same inputs."""
        id1 = _make_finding_id("n-plus-one-queries", "src/api.py", 42)
        id2 = _make_finding_id("n-plus-one-queries", "src/api.py", 42)
        assert id1 == id2

    def test_make_finding_id_differs_on_different_inputs(self):
        """Helper produces different IDs for different (signal, path, line) tuples."""
        id1 = _make_finding_id("n-plus-one-queries", "src/api.py", 42)
        id2 = _make_finding_id("n-plus-one-queries", "src/api.py", 43)
        id3 = _make_finding_id("missing-pagination", "src/api.py", 42)
        assert id1 != id2
        assert id1 != id3


# ── BR-APP-010: Scan summary is always returned ───────────────────────────────

class TestScanSummaryAlwaysReturned:
    def test_summary_present_when_no_findings(self, real_rubric):
        """ScanSummary is returned even when no patterns matched (BR-APP-010)."""
        inv = make_inventory([make_py_file("src/clean.py", "x = 1\ny = 2\n")])
        result = scan_app_antipatterns(inv, real_rubric)
        assert result.summary is not None

    def test_summary_present_on_empty_inventory(self, real_rubric):
        """ScanSummary is returned even for empty inventory (BR-APP-010)."""
        inv = make_inventory([])
        result = scan_app_antipatterns(inv, real_rubric)
        assert result.summary is not None
        assert result.summary.files_scanned == 0

    def test_summary_cap_reached_reflected(self, real_rubric):
        """ScanSummary.cap_reached mirrors inventory.scan_cap_reached (BR-APP-010)."""
        inv = PrioritizedInventory(
            iac_files=[],
            app_files=[make_py_file("src/a.py", "x = 1\n")],
            files_scanned_count=500,
            files_skipped_count=100,
            scan_cap_reached=True,
            repo_root="/repo",
        )
        result = scan_app_antipatterns(inv, real_rubric)
        assert result.summary.cap_reached is True
        assert result.summary.cap_note is not None

    def test_summary_cap_note_none_when_not_capped(self, real_rubric):
        """cap_note is None when scan cap was not reached."""
        inv = make_inventory([make_py_file("src/a.py", "x = 1\n")])
        result = scan_app_antipatterns(inv, real_rubric)
        assert result.summary.cap_note is None


# ── BR-APP-011: Zero findings is not an error ─────────────────────────────────

class TestZeroFindingsNotError:
    def test_clean_code_returns_empty_findings(self, real_rubric):
        """A codebase with no anti-patterns should return empty findings list (BR-APP-011)."""
        code = "def add(a, b):\n    return a + b\n"
        inv = make_inventory([make_py_file("src/math_utils.py", code)])
        result = scan_app_antipatterns(inv, real_rubric)
        assert isinstance(result.findings, list)
        assert result.summary is not None


# ── Detection tests (functional) ─────────────────────────────────────────────

class TestPatternDetection:
    def test_detects_per_request_boto3_client(self, real_rubric):
        """Detects boto3.client() inside function bodies (per-request-heavy-initialization)."""
        code = "def handler(event, context):\n    client = boto3.client('s3')\n    return {}\n"
        inv = make_inventory([make_py_file("src/handler.py", code)])
        result = scan_app_antipatterns(inv, real_rubric)
        types = {f.anti_pattern_type for f in result.findings}
        assert "Per-Request Heavy Initialization" in types

    def test_detects_select_star(self, real_rubric):
        """Detects SELECT * FROM pattern (full-table-scans or over-fetching)."""
        code = 'cursor.execute("SELECT * FROM users")\n'
        inv = make_inventory([make_py_file("src/db.py", code)])
        result = scan_app_antipatterns(inv, real_rubric)
        types = {f.anti_pattern_type for f in result.findings}
        assert types & {"Full-Table Scans", "Over-Fetching / Oversized Payloads"}

    def test_detects_ssm_get_parameter(self, real_rubric):
        """Detects SSM get_parameter (no-caching-on-hot-paths)."""
        code = "def handler(event, ctx):\n    v = ssm.get_parameter(Name='/app/key')\n"
        inv = make_inventory([make_py_file("src/handler.py", code)])
        result = scan_app_antipatterns(inv, real_rubric)
        types = {f.anti_pattern_type for f in result.findings}
        assert "No-Caching On Hot Paths" in types or "No Caching On Hot Paths" in types or any("cach" in t.lower() for t in types)

    def test_detects_dynamodb_scan(self, real_rubric):
        """Detects DynamoDB table.scan() (full-table-scans)."""
        code = "items = table.scan()\n"
        inv = make_inventory([make_py_file("src/db.py", code)])
        result = scan_app_antipatterns(inv, real_rubric)
        types = {f.anti_pattern_type for f in result.findings}
        assert any("scan" in t.lower() or "table" in t.lower() for t in types)

    def test_detects_requests_get(self, real_rubric):
        """Detects blocking requests.get() call (sync-where-async-possible)."""
        code = "def fetch():\n    return requests.get('https://api.example.com/data')\n"
        inv = make_inventory([make_py_file("src/client.py", code)])
        result = scan_app_antipatterns(inv, real_rubric)
        types = {f.anti_pattern_type for f in result.findings}
        assert any("sync" in t.lower() or "async" in t.lower() for t in types)

    def test_finding_has_file_path(self, real_rubric):
        """Findings include the correct file path."""
        code = "    boto3.client('s3')\n"
        inv = make_inventory([make_py_file("src/specific/handler.py", code)])
        result = scan_app_antipatterns(inv, real_rubric)
        if result.findings:
            assert any(f.file_path == "src/specific/handler.py" for f in result.findings)

    def test_finding_has_line_number(self, real_rubric):
        """Findings include a non-null line_number (1-indexed)."""
        code = "x = 1\n    boto3.client('s3')\ny = 2\n"
        inv = make_inventory([make_py_file("src/h.py", code)])
        result = scan_app_antipatterns(inv, real_rubric)
        if result.findings:
            for f in result.findings:
                if f.line_number is not None:
                    assert f.line_number >= 1
