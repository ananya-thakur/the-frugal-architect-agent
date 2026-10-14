"""Security tests for rubric-core: SR-RC-001 through SR-RC-005."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.rubric_core.engine import get_anti_pattern_signals, load_rubric
from agent.rubric_core.models import RubricLoadError


class TestSafeYAMLParsing:
    """SR-RC-001: yaml.safe_load must prevent Python tag execution."""

    def test_unsafe_python_object_tag_raises_rubric_load_error(
        self, tmp_path: Path
    ) -> None:
        """A YAML file containing !!python/object tags must raise RubricLoadError,
        not execute the tag.  yaml.safe_load() rejects all Python-specific tags."""
        bad = tmp_path / "rubric.yaml"
        bad.write_text(
            "key: !!python/object/apply:os.system ['id']\n", encoding="utf-8"
        )
        with pytest.raises(RubricLoadError):
            load_rubric(bad)

    def test_unsafe_python_object_new_tag_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "rubric.yaml"
        bad.write_text(
            "key: !!python/object/new:subprocess.Popen\n  args: [['id']]\n",
            encoding="utf-8",
        )
        with pytest.raises(RubricLoadError):
            load_rubric(bad)


class TestFileSizeCap:
    """SR-RC-002: files over 1 MB must be rejected before parsing."""

    def test_file_over_1mb_raises_rubric_load_error(self, tmp_path: Path) -> None:
        oversized = tmp_path / "rubric.yaml"
        oversized.write_bytes(b"x" * 1_100_000)
        with pytest.raises(RubricLoadError, match="exceeds maximum allowed size of 1 MB"):
            load_rubric(oversized)

    def test_file_exactly_at_limit_raises(self, tmp_path: Path) -> None:
        at_limit = tmp_path / "rubric.yaml"
        at_limit.write_bytes(b"x" * 1_048_577)  # 1 byte over
        with pytest.raises(RubricLoadError, match="exceeds maximum allowed size"):
            load_rubric(at_limit)

    def test_file_just_under_limit_proceeds_to_yaml_parse(
        self, tmp_path: Path
    ) -> None:
        under = tmp_path / "rubric.yaml"
        under.write_bytes(b"x" * 1_048_576)  # exactly at limit — allowed
        # File is valid size but not valid YAML → should reach yaml parse, not size gate
        with pytest.raises(RubricLoadError) as exc_info:
            load_rubric(under)
        assert "exceeds" not in str(exc_info.value)


class TestAttributeIntegrity:
    """SR-RC-004: attribution must be non-empty; all models must be frozen."""

    def test_blank_attribution_raises_rubric_load_error(
        self, tmp_path: Path, minimal_rubric_yaml: str
    ) -> None:
        import yaml

        data = yaml.safe_load(minimal_rubric_yaml)
        data["attribution"] = ""
        f = tmp_path / "rubric.yaml"
        f.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(RubricLoadError):
            load_rubric(f)

    def test_frozen_rubric_mutation_raises(self, rubric_yaml_file: Path) -> None:
        rubric = load_rubric(rubric_yaml_file)
        with pytest.raises((ValidationError, TypeError)):
            rubric.attribution = "mutated"  # type: ignore[misc]

    def test_frozen_law_definition_mutation_raises(
        self, rubric_yaml_file: Path
    ) -> None:
        rubric = load_rubric(rubric_yaml_file)
        law = rubric.laws[0]
        with pytest.raises((ValidationError, TypeError)):
            law.title = "mutated"  # type: ignore[misc]


class TestCacheMutationPrevention:
    """SR-RC-005: get_anti_pattern_signals returns a copy; models are frozen."""

    def test_signal_list_is_copy(self, rubric_yaml_file: Path) -> None:
        load_rubric(rubric_yaml_file)
        signals = get_anti_pattern_signals()
        original_len = len(signals)
        signals.append(signals[0])
        assert len(get_anti_pattern_signals()) == original_len

    def test_frozen_anti_pattern_signal_mutation_raises(
        self, rubric_yaml_file: Path
    ) -> None:
        load_rubric(rubric_yaml_file)
        signal = get_anti_pattern_signals()[0]
        with pytest.raises((ValidationError, TypeError)):
            signal.name = "mutated"  # type: ignore[misc]
