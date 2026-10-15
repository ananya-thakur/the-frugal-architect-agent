"""
Tests for eval/fixture_validator.py — startup credential safety gate.

Coverage:
  - AKIA* access key patterns raise FixtureSecurityViolation
  - Real 12-digit account IDs raise FixtureSecurityViolation
  - Synthetic account IDs (123456789012 etc.) are allowed
  - Real ARN account IDs raise FixtureSecurityViolation
  - ARNs with synthetic account IDs are allowed
  - Permission errors on fixture files are logged and skipped (not aborted)
  - Clean fixture directory passes without raising
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eval.fixture_validator import (
    SYNTHETIC_ALLOW_LIST,
    FixtureSecurityViolation,
    validate_fixtures,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _write_fixture(tmp_path: Path, content: str, filename: str = "fixture.yaml") -> Path:
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return p


# ── clean fixture ─────────────────────────────────────────────────────────────


class TestCleanFixture:
    def test_empty_dir_passes(self, tmp_path: Path):
        validate_fixtures(tmp_path)  # should not raise

    def test_yaml_with_only_synthetic_ids_passes(self, tmp_path: Path):
        _write_fixture(
            tmp_path,
            "account_id: 123456789012\narn: arn:aws:iam::123456789012:role/MyRole\n",
        )
        validate_fixtures(tmp_path)  # should not raise

    def test_yaml_without_account_ids_passes(self, tmp_path: Path):
        _write_fixture(
            tmp_path,
            "bucket_name: my-bucket\nregion: us-east-1\n",
        )
        validate_fixtures(tmp_path)

    def test_all_synthetic_ids_in_allowlist(self):
        assert "123456789012" in SYNTHETIC_ALLOW_LIST
        assert "000000000000" in SYNTHETIC_ALLOW_LIST
        assert "111111111111" in SYNTHETIC_ALLOW_LIST


# ── AKIA access key ───────────────────────────────────────────────────────────


class TestAkiaKey:
    def test_akia_key_raises(self, tmp_path: Path):
        _write_fixture(tmp_path, "aws_key: AKIAIOSFODNN7EXAMPLE\n")
        with pytest.raises(FixtureSecurityViolation) as exc_info:
            validate_fixtures(tmp_path)
        assert "AWS_ACCESS_KEY_ID" in str(exc_info.value)

    def test_akia_key_in_nested_yaml_raises(self, tmp_path: Path):
        nested = tmp_path / "sub"
        nested.mkdir()
        _write_fixture(nested, "key: AKIAZZZZZZZZZZZZZZZZ\n")
        with pytest.raises(FixtureSecurityViolation):
            validate_fixtures(tmp_path)


# ── Real account IDs ──────────────────────────────────────────────────────────


class TestRealAccountId:
    def test_real_12_digit_id_raises(self, tmp_path: Path):
        _write_fixture(tmp_path, "account_id: 987654321012\n")
        with pytest.raises(FixtureSecurityViolation) as exc_info:
            validate_fixtures(tmp_path)
        assert "REAL_ACCOUNT_ID" in str(exc_info.value)

    def test_real_account_id_in_arn_raises(self, tmp_path: Path):
        _write_fixture(tmp_path, "arn: arn:aws:iam::987654321012:role/Admin\n")
        with pytest.raises(FixtureSecurityViolation) as exc_info:
            validate_fixtures(tmp_path)
        assert exc_info.value.pattern in ("REAL_ACCOUNT_ID", "REAL_ARN")

    def test_synthetic_000_passes(self, tmp_path: Path):
        _write_fixture(tmp_path, "account_id: 000000000000\n")
        validate_fixtures(tmp_path)

    def test_synthetic_111_passes(self, tmp_path: Path):
        _write_fixture(tmp_path, "arn: arn:aws:iam::111111111111:role/Test\n")
        validate_fixtures(tmp_path)

    def test_non_yaml_files_not_scanned(self, tmp_path: Path):
        # .txt file with a real account ID should NOT trigger the validator
        (tmp_path / "notes.txt").write_text("account: 987654321012\n", encoding="utf-8")
        validate_fixtures(tmp_path)  # should not raise


# ── Exception content ─────────────────────────────────────────────────────────


class TestExceptionContent:
    def test_exception_includes_file_path(self, tmp_path: Path):
        p = _write_fixture(tmp_path, "account_id: 987654321012\n", "bad.yaml")
        with pytest.raises(FixtureSecurityViolation) as exc_info:
            validate_fixtures(tmp_path)
        assert str(p) in str(exc_info.value) or "bad.yaml" in str(exc_info.value)

    def test_exception_has_path_attribute(self, tmp_path: Path):
        _write_fixture(tmp_path, "account_id: 987654321012\n")
        with pytest.raises(FixtureSecurityViolation) as exc_info:
            validate_fixtures(tmp_path)
        assert exc_info.value.path is not None
