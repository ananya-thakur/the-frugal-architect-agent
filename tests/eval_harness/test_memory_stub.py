"""
Tests for eval/memory_stub.py — in-process memory stub.

Coverage:
  - store_session / get_session round-trip
  - store_long_term / get_long_term round-trip
  - Missing keys return None
  - resolve_reference always returns None
  - cleanup_namespace removes only matching keys
  - Two separate instances are independent (no shared state)
"""

from __future__ import annotations

import pytest

from eval.memory_stub import InProcessMemoryStub


class TestInProcessMemoryStub:
    def test_session_roundtrip(self):
        stub = InProcessMemoryStub()
        stub.store_session("s1", {"turn": 1, "data": "hello"})
        assert stub.get_session("s1") == {"turn": 1, "data": "hello"}

    def test_session_missing_returns_none(self):
        stub = InProcessMemoryStub()
        assert stub.get_session("nonexistent") is None

    def test_session_overwrite(self):
        stub = InProcessMemoryStub()
        stub.store_session("s1", "first")
        stub.store_session("s1", "second")
        assert stub.get_session("s1") == "second"

    def test_long_term_roundtrip(self):
        stub = InProcessMemoryStub()
        stub.store_long_term("user-42", "last_review", {"score": "PARTIAL"})
        assert stub.get_long_term("user-42", "last_review") == {"score": "PARTIAL"}

    def test_long_term_missing_returns_none(self):
        stub = InProcessMemoryStub()
        assert stub.get_long_term("user-99", "missing") is None

    def test_long_term_different_users_isolated(self):
        stub = InProcessMemoryStub()
        stub.store_long_term("user-1", "key", "value-for-1")
        stub.store_long_term("user-2", "key", "value-for-2")
        assert stub.get_long_term("user-1", "key") == "value-for-1"
        assert stub.get_long_term("user-2", "key") == "value-for-2"

    def test_resolve_reference_always_none(self):
        stub = InProcessMemoryStub()
        assert stub.resolve_reference("anything") is None

    def test_cleanup_namespace_removes_matching_keys(self):
        stub = InProcessMemoryStub()
        stub.store_session("eval-abc-t1", "data1")
        stub.store_session("eval-abc-t2", "data2")
        stub.store_session("prod-session", "keep")
        stub.cleanup_namespace("eval-abc")
        assert stub.get_session("eval-abc-t1") is None
        assert stub.get_session("eval-abc-t2") is None
        assert stub.get_session("prod-session") == "keep"

    def test_cleanup_empty_namespace_no_error(self):
        stub = InProcessMemoryStub()
        stub.cleanup_namespace("nothing-matching")  # should not raise

    def test_two_instances_are_independent(self):
        a = InProcessMemoryStub()
        b = InProcessMemoryStub()
        a.store_session("shared-key", "value-a")
        assert b.get_session("shared-key") is None
