"""Tests for validate_citation and get_anti_pattern_signals."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.rubric_core.engine import (
    get_anti_pattern_signals,
    load_rubric,
    validate_citation,
)
from agent.rubric_core.models import AntiPatternSignal


@pytest.fixture(autouse=True)
def load_fixture_rubric(rubric_yaml_file: Path) -> None:
    load_rubric(rubric_yaml_file)


class TestValidateCitation:
    def test_correct_number_and_title_returns_true(self) -> None:
        # Fixture titles are "Law N Title"
        assert validate_citation(1, "Law 1 Title") is True

    def test_correct_all_laws(self) -> None:
        for n in range(1, 8):
            assert validate_citation(n, f"Law {n} Title") is True

    def test_wrong_title_for_correct_number_returns_false(self) -> None:
        assert validate_citation(1, "Wrong Title") is False

    def test_correct_title_for_wrong_number_returns_false(self) -> None:
        # Law 2's title does not match law number 1
        assert validate_citation(1, "Law 2 Title") is False

    def test_non_existent_number_returns_false(self) -> None:
        assert validate_citation(0, "Law 1 Title") is False
        assert validate_citation(8, "Law 1 Title") is False

    def test_case_insensitive_title_match(self) -> None:
        assert validate_citation(1, "law 1 title") is True
        assert validate_citation(3, "LAW 3 TITLE") is True

    def test_whitespace_stripped_on_both_sides(self) -> None:
        assert validate_citation(2, "  Law 2 Title  ") is True


class TestGetAntiPatternSignals:
    def test_returns_exactly_10_signals(self) -> None:
        signals = get_anti_pattern_signals()
        assert len(signals) == 10

    def test_returns_list_of_anti_pattern_signal(self) -> None:
        signals = get_anti_pattern_signals()
        for s in signals:
            assert isinstance(s, AntiPatternSignal)

    def test_all_signals_have_non_empty_id(self) -> None:
        for s in get_anti_pattern_signals():
            assert s.id

    def test_all_signals_have_matching_criteria(self) -> None:
        for s in get_anti_pattern_signals():
            assert s.matching_criteria

    def test_all_signals_have_related_laws(self) -> None:
        for s in get_anti_pattern_signals():
            assert len(s.related_laws) >= 1

    def test_returns_copy_not_original(self) -> None:
        """Appending to returned list must not affect the next call."""
        signals = get_anti_pattern_signals()
        signals.append(signals[0])  # mutate the returned list
        assert len(get_anti_pattern_signals()) == 10

    def test_two_calls_return_equal_content(self) -> None:
        s1 = get_anti_pattern_signals()
        s2 = get_anti_pattern_signals()
        assert [s.id for s in s1] == [s.id for s in s2]
