"""Tests for get_law: lookup by int, numeric string, title, and error paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.rubric_core.engine import get_law, load_rubric
from agent.rubric_core.models import LawDefinition, UnknownLawError


@pytest.fixture(autouse=True)
def load_fixture_rubric(rubric_yaml_file: Path) -> None:
    """Pre-load the minimal fixture rubric before each get_law test."""
    load_rubric(rubric_yaml_file)


class TestGetLawByInt:
    def test_lookup_law_1(self) -> None:
        law = get_law(1)
        assert isinstance(law, LawDefinition)
        assert law.number == 1

    def test_lookup_law_7(self) -> None:
        law = get_law(7)
        assert law.number == 7

    def test_each_law_number_resolves(self) -> None:
        for n in range(1, 8):
            law = get_law(n)
            assert law.number == n

    def test_unknown_int_0_raises(self) -> None:
        with pytest.raises(UnknownLawError, match="1–7"):
            get_law(0)

    def test_unknown_int_8_raises(self) -> None:
        with pytest.raises(UnknownLawError, match="1–7"):
            get_law(8)


class TestGetLawByNumericString:
    def test_numeric_string_resolves(self) -> None:
        law = get_law("3")
        assert law.number == 3

    def test_numeric_string_7(self) -> None:
        law = get_law("7")
        assert law.number == 7

    def test_out_of_range_numeric_string_raises(self) -> None:
        with pytest.raises(UnknownLawError):
            get_law("9")


class TestGetLawByTitle:
    def test_exact_title_match(self) -> None:
        law = get_law("Law 1 Title")
        assert law.number == 1

    def test_case_insensitive_exact_match(self) -> None:
        law = get_law("law 1 title")
        assert law.number == 1

    def test_case_insensitive_upper(self) -> None:
        law = get_law("LAW 1 TITLE")
        assert law.number == 1

    def test_substring_match(self) -> None:
        # "Law 2" is a substring of "Law 2 Title"
        law = get_law("Law 2")
        assert law.number == 2

    def test_unrecognised_title_raises(self) -> None:
        with pytest.raises(UnknownLawError):
            get_law("Completely Unknown Law Title")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(UnknownLawError):
            get_law("")


class TestGetLawReturnType:
    def test_returns_law_definition(self) -> None:
        law = get_law(1)
        assert hasattr(law, "number")
        assert hasattr(law, "title")
        assert hasattr(law, "description")
        assert hasattr(law, "strong_looks_like")
        assert hasattr(law, "probing_questions")
        assert isinstance(law.probing_questions, list)
        assert len(law.probing_questions) >= 1
