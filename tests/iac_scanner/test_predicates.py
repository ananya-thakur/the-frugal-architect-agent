"""Tests for the predicate evaluator (agent/iac_scanner/predicates.py)."""

from __future__ import annotations

import pytest

from agent.iac_scanner.predicates import PredicateError, evaluate, resolve_attribute


class TestNullPredicates:
    def test_eq_null_with_none_returns_true(self) -> None:
        assert evaluate("== null", None) is True

    def test_eq_null_with_value_returns_false(self) -> None:
        assert evaluate("== null", "something") is False

    def test_eq_null_case_insensitive(self) -> None:
        assert evaluate("== NULL", None) is True

    def test_neq_null_with_none_returns_false(self) -> None:
        assert evaluate("!= null", None) is False

    def test_neq_null_with_value_returns_true(self) -> None:
        assert evaluate("!= null", "Active") is True


class TestNumericPredicates:
    def test_gt_true(self) -> None:
        assert evaluate("> 1024", 1536) is True

    def test_gt_false(self) -> None:
        assert evaluate("> 1024", 512) is False

    def test_gt_equal_boundary_is_false(self) -> None:
        assert evaluate("> 1024", 1024) is False

    def test_gt_none_returns_false(self) -> None:
        assert evaluate("> 1024", None) is False

    def test_lt_true(self) -> None:
        assert evaluate("< 100", 50) is True

    def test_lt_false(self) -> None:
        assert evaluate("< 100", 200) is False

    def test_lt_none_returns_false(self) -> None:
        assert evaluate("< 100", None) is False

    def test_gte_true(self) -> None:
        assert evaluate(">= 1024", 1024) is True

    def test_lte_true(self) -> None:
        assert evaluate("<= 512", 512) is True

    def test_numeric_string_value_coerced(self) -> None:
        assert evaluate("> 1024", "1536") is True

    def test_non_numeric_value_returns_false(self) -> None:
        assert evaluate("> 1024", "not-a-number") is False


class TestStringPredicates:
    def test_neq_string_different_returns_true(self) -> None:
        assert evaluate("!= Active", "PassThrough") is True

    def test_neq_string_same_returns_false(self) -> None:
        assert evaluate("!= Active", "Active") is False

    def test_neq_string_none_returns_true(self) -> None:
        # Absent attribute ≠ expected value → gap flagged
        assert evaluate("!= Active", None) is True

    def test_eq_string_same_returns_true(self) -> None:
        assert evaluate("== Active", "Active") is True

    def test_eq_string_different_returns_false(self) -> None:
        assert evaluate("== Active", "PassThrough") is False

    def test_eq_string_none_returns_false(self) -> None:
        assert evaluate("== Active", None) is False

    def test_neq_quoted_string(self) -> None:
        assert evaluate("!= 'arm64'", "x86_64") is True

    def test_neq_quoted_string_match(self) -> None:
        assert evaluate("!= 'arm64'", "arm64") is False


class TestPredicateErrors:
    def test_unknown_predicate_raises(self) -> None:
        with pytest.raises(PredicateError, match="Unrecognised"):
            evaluate("CONTAINS something", "value")


class TestResolveAttribute:
    def test_top_level_key(self) -> None:
        assert resolve_attribute({"a": 1}, "a") == 1

    def test_nested_key(self) -> None:
        assert resolve_attribute({"a": {"b": 2}}, "a.b") == 2

    def test_missing_key_returns_none(self) -> None:
        assert resolve_attribute({"a": 1}, "b") is None

    def test_missing_nested_key_returns_none(self) -> None:
        assert resolve_attribute({"a": {}}, "a.b") is None

    def test_intermediate_non_dict_returns_none(self) -> None:
        assert resolve_attribute({"a": "scalar"}, "a.b") is None

    def test_empty_dict_returns_none(self) -> None:
        assert resolve_attribute({}, "a") is None
