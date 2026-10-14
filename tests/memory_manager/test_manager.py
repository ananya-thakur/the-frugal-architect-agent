"""MemoryManager unit tests — covers all five methods and the security matrix.

Security test coverage (SEC-MEM-006, SEC-MEM-007, SEC-MEM-008, SEC-MEM-009):
  - TypeError on untyped store inputs
  - None return on any deserialisation failure
  - top_finding_summaries truncated to 10; caller object unchanged
  - No user_id or scorecard content in WARNING log output

Isolation tests (SEC-MEM-003, SEC-MEM-004):
  - Session namespace write not visible in long_term namespace
  - Different user IDs produce independent long-term entries

Business-rule tests (BR-MEM-009 through BR-MEM-019):
  - API errors → WARNING logged, no exception propagated
  - get_long_term miss → None (first-time user, not an error)
  - store_long_term stamps last_review_at at write time
  - resolve_reference decision tree
  - LawScore takes precedence over Finding in reference resolution
"""

import dataclasses
import json
import logging

import pytest

from agent.memory_manager.entities import Finding, LawScore, Scorecard, ScorecardSummary
from agent.memory_manager.manager import MemoryManager
from agent.memory_manager.stub import ErroringStub, InMemoryStub
from tests.memory_manager.conftest import make_scorecard, make_summary


# ── store_session / get_session ───────────────────────────────────────────────


class TestStoreGetSession:
    def test_roundtrip(self, manager: MemoryManager) -> None:
        sc = make_scorecard("sess-001")
        manager.store_session("sess-001", sc)
        retrieved = manager.get_session("sess-001")
        assert retrieved is not None
        assert retrieved.session_id == "sess-001"
        assert len(retrieved.law_scores) == 7
        assert len(retrieved.findings) == 2

    def test_miss_returns_none(self, manager: MemoryManager) -> None:
        assert manager.get_session("nonexistent") is None

    def test_type_error_on_dict(self, manager: MemoryManager) -> None:
        with pytest.raises(TypeError, match="expected Scorecard"):
            manager.store_session("sess-001", {"not": "a scorecard"})  # type: ignore[arg-type]

    def test_api_error_on_store_does_not_raise(self) -> None:
        mgr = MemoryManager(api=ErroringStub())
        sc = make_scorecard()
        mgr.store_session("sess-001", sc)  # must not raise

    def test_api_error_on_store_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        mgr = MemoryManager(api=ErroringStub())
        with caplog.at_level(logging.WARNING, logger="agent.memory_manager.manager"):
            mgr.store_session("sess-001", make_scorecard())
        assert any("store_session" in r.message and "api_error" in r.message for r in caplog.records)

    def test_api_error_on_get_returns_none(self) -> None:
        mgr = MemoryManager(api=ErroringStub())
        assert mgr.get_session("sess-001") is None

    def test_malformed_json_returns_none(self, stub: InMemoryStub, manager: MemoryManager) -> None:
        stub.put("session", "sess-bad", "not-json-{{{")
        assert manager.get_session("sess-bad") is None

    def test_schema_mismatch_returns_none(self, stub: InMemoryStub, manager: MemoryManager) -> None:
        stub.put("session", "sess-wrong", json.dumps({"unexpected": "shape"}))
        assert manager.get_session("sess-wrong") is None

    def test_deserialization_error_logs_warning(
        self, stub: InMemoryStub, manager: MemoryManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        stub.put("session", "sess-bad", "{{invalid")
        with caplog.at_level(logging.WARNING, logger="agent.memory_manager.manager"):
            manager.get_session("sess-bad")
        assert any("deserialization_error" in r.message for r in caplog.records)


# ── store_long_term / get_long_term ───────────────────────────────────────────


class TestStoreGetLongTerm:
    def test_roundtrip(self, manager: MemoryManager) -> None:
        summary = make_summary("user-1", "proj-1")
        manager.store_long_term("user-1", "proj-1", summary)
        retrieved = manager.get_long_term("user-1", "proj-1")
        assert retrieved is not None
        assert retrieved.user_id == "user-1"
        assert retrieved.project_id == "proj-1"
        assert retrieved.law_verdicts == summary.law_verdicts

    def test_miss_returns_none_for_first_time_user(self, manager: MemoryManager) -> None:
        result = manager.get_long_term("new-user", "new-proj")
        assert result is None

    def test_type_error_on_dict(self, manager: MemoryManager) -> None:
        with pytest.raises(TypeError, match="expected ScorecardSummary"):
            manager.store_long_term("u", "p", {"not": "a summary"})  # type: ignore[arg-type]

    def test_stamps_last_review_at(self, manager: MemoryManager) -> None:
        summary = make_summary()
        assert summary.last_review_at == ""
        manager.store_long_term("user-1", "proj-1", summary)
        retrieved = manager.get_long_term("user-1", "proj-1")
        assert retrieved is not None
        assert retrieved.last_review_at != ""
        assert "T" in retrieved.last_review_at  # ISO 8601 timestamp

    def test_stamps_last_review_at_overwrites_caller_value(
        self, manager: MemoryManager
    ) -> None:
        summary = dataclasses.replace(make_summary(), last_review_at="2000-01-01T00:00:00")
        manager.store_long_term("user-1", "proj-1", summary)
        retrieved = manager.get_long_term("user-1", "proj-1")
        assert retrieved is not None
        assert retrieved.last_review_at != "2000-01-01T00:00:00"

    def test_truncates_top_findings_to_10(self, manager: MemoryManager) -> None:
        summary = dataclasses.replace(
            make_summary(),
            top_finding_summaries=[f"finding-{i}" for i in range(15)],
        )
        manager.store_long_term("u", "p", summary)
        retrieved = manager.get_long_term("u", "p")
        assert retrieved is not None
        assert len(retrieved.top_finding_summaries) == 10

    def test_truncation_does_not_mutate_caller_object(self, manager: MemoryManager) -> None:
        long_list = [f"finding-{i}" for i in range(15)]
        summary = dataclasses.replace(make_summary(), top_finding_summaries=long_list)
        manager.store_long_term("u", "p", summary)
        assert len(summary.top_finding_summaries) == 15  # caller's copy unchanged

    def test_api_error_on_store_does_not_raise(self) -> None:
        mgr = MemoryManager(api=ErroringStub())
        mgr.store_long_term("u", "p", make_summary())  # must not raise

    def test_api_error_on_get_returns_none(self) -> None:
        mgr = MemoryManager(api=ErroringStub())
        assert mgr.get_long_term("u", "p") is None

    def test_deserialization_error_returns_none(
        self, stub: InMemoryStub, manager: MemoryManager
    ) -> None:
        stub.put("long_term", "user-1:proj-1", json.dumps({"incomplete": True}))
        assert manager.get_long_term("user-1", "proj-1") is None


# ── Isolation tests ───────────────────────────────────────────────────────────


class TestIsolation:
    def test_composite_key_user_isolation(self, manager: MemoryManager) -> None:
        """(user_A, proj) and (user_B, proj) must be independent entries."""
        summary_a = dataclasses.replace(make_summary(), user_id="user-A")
        summary_b = dataclasses.replace(
            make_summary(), user_id="user-B", overall_verdict="STRONG"
        )
        manager.store_long_term("user-A", "proj-shared", summary_a)
        manager.store_long_term("user-B", "proj-shared", summary_b)

        result_a = manager.get_long_term("user-A", "proj-shared")
        result_b = manager.get_long_term("user-B", "proj-shared")

        assert result_a is not None and result_a.overall_verdict == "PARTIAL — 2 gaps, 5 strong"
        assert result_b is not None and result_b.overall_verdict == "STRONG"

    def test_namespace_isolation_session_vs_long_term(
        self, stub: InMemoryStub, manager: MemoryManager
    ) -> None:
        """A session write must not be visible in the long_term namespace."""
        sc = make_scorecard("sess-001")
        manager.store_session("sess-001", sc)

        # session namespace has the key; long_term namespace does not
        assert "sess-001" in stub.keys("session")
        assert "sess-001" not in stub.keys("long_term")


# ── Log sanitisation ──────────────────────────────────────────────────────────


class TestLogSanitisation:
    def test_api_error_log_contains_no_user_id(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """WARNING log on store_long_term must not include the user_id (SEC-MEM-009)."""
        mgr = MemoryManager(api=ErroringStub())
        sensitive_user_id = "user-very-sensitive-12345"
        with caplog.at_level(logging.WARNING, logger="agent.memory_manager.manager"):
            mgr.store_long_term(sensitive_user_id, "proj-1", make_summary())
        for record in caplog.records:
            assert sensitive_user_id not in record.message

    def test_api_error_log_contains_no_scorecard_content(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """WARNING log on store_session must not contain scorecard payload content."""
        mgr = MemoryManager(api=ErroringStub())
        sc = make_scorecard()
        with caplog.at_level(logging.WARNING, logger="agent.memory_manager.manager"):
            mgr.store_session("sess-001", sc)
        for record in caplog.records:
            assert sc.overall_verdict not in record.message
            assert sc.biggest_opportunity not in record.message


# ── resolve_reference ─────────────────────────────────────────────────────────


class TestResolveReference:
    def _setup(self, manager: MemoryManager) -> None:
        """Store a fixture scorecard so resolve_reference has a session to search."""
        manager.store_session("sess-ref", make_scorecard("sess-ref"))

    def test_law_number_arabic(self, manager: MemoryManager) -> None:
        self._setup(manager)
        result = manager.resolve_reference("sess-ref", "show me Law 4")
        assert isinstance(result, LawScore)
        assert result.law_number == 4

    def test_law_number_roman_numeral(self, manager: MemoryManager) -> None:
        self._setup(manager)
        result = manager.resolve_reference("sess-ref", "explain Law V")
        assert isinstance(result, LawScore)
        assert result.law_number == 5

    def test_law_number_roman_lower(self, manager: MemoryManager) -> None:
        self._setup(manager)
        result = manager.resolve_reference("sess-ref", "law ii details")
        assert isinstance(result, LawScore)
        assert result.law_number == 2

    def test_law_title_keyword_match(self, manager: MemoryManager) -> None:
        self._setup(manager)
        # "Cost Aware" appears in law 2's title — two matching tokens
        result = manager.resolve_reference("sess-ref", "what about cost aware systems?")
        assert isinstance(result, LawScore)
        assert result.law_number == 2

    def test_anti_pattern_type_match(self, manager: MemoryManager) -> None:
        self._setup(manager)
        result = manager.resolve_reference("sess-ref", "that n_plus_one_query problem")
        assert isinstance(result, Finding)
        assert result.anti_pattern_type == "n_plus_one_query"

    def test_finding_summary_keyword_match(self, manager: MemoryManager) -> None:
        self._setup(manager)
        # "cost allocation tags" — multiple tokens match Finding f-001 summary
        result = manager.resolve_reference("sess-ref", "tell me about cost allocation tags")
        assert isinstance(result, Finding)
        assert result.id == "f-001"

    def test_no_session_returns_none(self, manager: MemoryManager) -> None:
        result = manager.resolve_reference("sess-does-not-exist", "Law 3")
        assert result is None

    def test_no_match_returns_none(self, manager: MemoryManager) -> None:
        self._setup(manager)
        result = manager.resolve_reference("sess-ref", "completely unrelated sentence xyz")
        assert result is None

    def test_law_takes_precedence_over_finding(self, manager: MemoryManager) -> None:
        """When a LawScore and a Finding could both match, LawScore wins (BR-MEM-018)."""
        self._setup(manager)
        # "Law 2" should return LawScore even though Finding f-001 is for law_number 2
        result = manager.resolve_reference("sess-ref", "Law 2 finding details")
        assert isinstance(result, LawScore)
        assert result.law_number == 2
