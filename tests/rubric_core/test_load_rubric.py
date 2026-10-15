"""Tests for load_rubric: happy path, error paths, caching, env override."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from agent.rubric_core.engine import load_rubric
from agent.rubric_core.models import Rubric, RubricLoadError


class TestLoadRubricHappyPath:
    def test_returns_rubric_instance(self, rubric_yaml_file: Path) -> None:
        rubric = load_rubric(rubric_yaml_file)
        assert isinstance(rubric, Rubric)

    def test_exactly_7_laws(self, rubric_yaml_file: Path) -> None:
        rubric = load_rubric(rubric_yaml_file)
        assert len(rubric.laws) == 7

    def test_law_numbers_are_1_to_7(self, rubric_yaml_file: Path) -> None:
        rubric = load_rubric(rubric_yaml_file)
        assert {law.number for law in rubric.laws} == {1, 2, 3, 4, 5, 6, 7}

    def test_exactly_10_anti_pattern_signals(self, rubric_yaml_file: Path) -> None:
        rubric = load_rubric(rubric_yaml_file)
        assert len(rubric.anti_pattern_signals) == 10

    def test_attribution_is_non_empty(self, rubric_yaml_file: Path) -> None:
        rubric = load_rubric(rubric_yaml_file)
        assert rubric.attribution

    def test_loads_bundled_rubric_when_no_path(self) -> None:
        """Bundled rubric.yaml (7 real laws) loads without error."""
        rubric = load_rubric()
        assert isinstance(rubric, Rubric)
        assert len(rubric.laws) == 7
        assert len(rubric.anti_pattern_signals) == 10


class TestLoadRubricErrorPaths:
    def test_missing_file_raises_rubric_load_error(self, tmp_path: Path) -> None:
        with pytest.raises(RubricLoadError, match="not found"):
            load_rubric(tmp_path / "nonexistent.yaml")

    def test_malformed_yaml_raises_rubric_load_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("key: [\nunclosed bracket", encoding="utf-8")
        with pytest.raises(RubricLoadError, match="not valid YAML"):
            load_rubric(bad)

    def test_non_mapping_yaml_raises_rubric_load_error(self, tmp_path: Path) -> None:
        f = tmp_path / "list.yaml"
        f.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(RubricLoadError, match="mapping"):
            load_rubric(f)

    def test_wrong_law_count_raises_rubric_load_error(
        self, tmp_path: Path, minimal_rubric_yaml: str
    ) -> None:
        # Replace the 7-law YAML with a 6-law version
        import yaml

        data = yaml.safe_load(minimal_rubric_yaml)
        data["laws"] = data["laws"][:6]
        bad = tmp_path / "rubric.yaml"
        bad.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(RubricLoadError, match="7"):
            load_rubric(bad)

    def test_wrong_signal_count_raises_rubric_load_error(
        self, tmp_path: Path, minimal_rubric_yaml: str
    ) -> None:
        import yaml

        data = yaml.safe_load(minimal_rubric_yaml)
        data["anti_pattern_signals"] = data["anti_pattern_signals"][:9]
        bad = tmp_path / "rubric.yaml"
        bad.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(RubricLoadError, match="10"):
            load_rubric(bad)

    def test_blank_attribution_raises_rubric_load_error(
        self, tmp_path: Path, minimal_rubric_yaml: str
    ) -> None:
        import yaml

        data = yaml.safe_load(minimal_rubric_yaml)
        data["attribution"] = ""
        bad = tmp_path / "rubric.yaml"
        bad.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(RubricLoadError):
            load_rubric(bad)

    def test_duplicate_law_numbers_raises_rubric_load_error(
        self, tmp_path: Path, minimal_rubric_yaml: str
    ) -> None:
        import yaml

        data = yaml.safe_load(minimal_rubric_yaml)
        data["laws"][6]["number"] = 1  # duplicate law 1
        bad = tmp_path / "rubric.yaml"
        bad.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(RubricLoadError):
            load_rubric(bad)


class TestLoadRubricEnvOverride:
    def test_rubric_path_env_overrides_bundled(
        self, rubric_yaml_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUBRIC_PATH", str(rubric_yaml_file))
        rubric = load_rubric()
        assert rubric.laws[0].title == "Law 1 Title"  # from fixture, not bundled

    def test_rubric_path_env_missing_file_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUBRIC_PATH", str(tmp_path / "missing.yaml"))
        with pytest.raises(RubricLoadError, match="not found"):
            load_rubric()


class TestLoadRubricCaching:
    def test_same_instance_returned_on_second_call(self, rubric_yaml_file: Path) -> None:
        r1 = load_rubric(rubric_yaml_file)
        r2 = load_rubric(rubric_yaml_file)
        assert r1 is r2

    def test_mtime_change_triggers_reload(
        self, tmp_path: Path, minimal_rubric_yaml: str
    ) -> None:
        import yaml

        f = tmp_path / "rubric.yaml"
        f.write_text(minimal_rubric_yaml, encoding="utf-8")
        r1 = load_rubric(f)
        assert r1.laws[0].title == "Law 1 Title"

        # Modify the file and force a different mtime
        data = yaml.safe_load(minimal_rubric_yaml)
        data["laws"][0]["title"] = "Updated Law 1"
        time.sleep(0.01)  # ensure mtime changes
        f.write_text(yaml.dump(data), encoding="utf-8")
        # Touch to ensure OS mtime is updated
        os.utime(f, None)

        r2 = load_rubric(f)
        assert r2 is not r1
        assert r2.laws[0].title == "Updated Law 1"
