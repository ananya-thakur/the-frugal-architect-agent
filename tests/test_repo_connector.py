"""
Tests for the repo-connector unit (unit-3).

Coverage:
- Domain entity invariants (types.py)
- IaC tier classification (all 8 pattern groups from business-logic-model)
- get_scan_limits() — env var handling, defaults
- prioritise_files() — IaC-first, alphabetical, caps, IaC-partial
- check_connectivity() — local path and URL cases
- _clone_or_read_repo_impl() — local path scan, subdirectory filter,
  cap enforcement, ScopeNote authoring, SCMNotConnectedError
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from agent.tools.repo_connector._patterns import classify_tier
from agent.tools.repo_connector.tool import (
    _clone_or_read_repo_impl,
    _resolve_repo_ref,
    check_connectivity,
    get_scan_limits,
    prioritise_files,
)
from agent.tools.repo_connector.types import (
    FileRecord,
    RawInventory,
    RepoInventory,
    RepoRef,
    SCMNotConnectedError,
    ScanLimits,
    ScopeNote,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file_record(
    path: str,
    tier: str = "app",
    content: str | None = None,
) -> FileRecord:
    actual = len(content) // 4 if content is not None else None
    estimated = actual if actual is not None else 0
    return FileRecord(
        path=path,
        tier=tier,  # type: ignore[arg-type]
        estimated_tokens=estimated,
        content=content,
        actual_tokens=actual,
    )


def _make_repo_ref(raw: str, subdirectory: str | None = None) -> RepoRef:
    return _resolve_repo_ref(raw, subdirectory)


# ---------------------------------------------------------------------------
# TestScanLimits
# ---------------------------------------------------------------------------

class TestScanLimits:
    def test_valid_construction(self) -> None:
        sl = ScanLimits(file_limit=100, token_budget=50_000)
        assert sl.file_limit == 100
        assert sl.token_budget == 50_000

    def test_zero_file_limit_rejected(self) -> None:
        with pytest.raises(ValueError, match="file_limit"):
            ScanLimits(file_limit=0, token_budget=50_000)

    def test_negative_file_limit_rejected(self) -> None:
        with pytest.raises(ValueError, match="file_limit"):
            ScanLimits(file_limit=-1, token_budget=50_000)

    def test_zero_token_budget_rejected(self) -> None:
        with pytest.raises(ValueError, match="token_budget"):
            ScanLimits(file_limit=100, token_budget=0)


# ---------------------------------------------------------------------------
# TestFileRecord
# ---------------------------------------------------------------------------

class TestFileRecord:
    def test_loaded_record_valid(self) -> None:
        rec = FileRecord(
            path="src/main.py",
            tier="app",
            estimated_tokens=10,
            content="hello world",
            actual_tokens=2,
        )
        assert rec.content == "hello world"
        assert rec.actual_tokens == 2

    def test_stub_record_valid(self) -> None:
        rec = FileRecord(
            path="src/large.py",
            tier="app",
            estimated_tokens=10,
            content=None,
            actual_tokens=None,
        )
        assert rec.content is None
        assert rec.actual_tokens is None

    def test_content_without_actual_tokens_rejected(self) -> None:
        with pytest.raises(ValueError):
            FileRecord(
                path="src/main.py",
                tier="app",
                estimated_tokens=10,
                content="hello",
                actual_tokens=None,
            )

    def test_actual_tokens_without_content_rejected(self) -> None:
        with pytest.raises(ValueError):
            FileRecord(
                path="src/main.py",
                tier="app",
                estimated_tokens=10,
                content=None,
                actual_tokens=5,
            )

    def test_absolute_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="relative"):
            FileRecord(
                path="/abs/path.py",
                tier="app",
                estimated_tokens=0,
                content=None,
                actual_tokens=None,
            )

    def test_negative_estimated_tokens_rejected(self) -> None:
        with pytest.raises(ValueError, match="estimated_tokens"):
            FileRecord(
                path="src/main.py",
                tier="app",
                estimated_tokens=-1,
                content=None,
                actual_tokens=None,
            )

    def test_immutable(self) -> None:
        rec = _make_file_record("src/main.py")
        with pytest.raises((AttributeError, TypeError)):
            rec.path = "other.py"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestScopeNote
# ---------------------------------------------------------------------------

class TestScopeNote:
    def test_uncapped_valid(self) -> None:
        sn = ScopeNote(
            capped=False,
            cap_type="none",
            files_included=10,
            files_seen=10,
            tokens_consumed=1000,
            layers_covered="full",
            subdirectory=None,
        )
        assert sn.capped is False

    def test_capped_file_count_valid(self) -> None:
        sn = ScopeNote(
            capped=True,
            cap_type="file_count",
            files_included=5,
            files_seen=20,
            tokens_consumed=500,
            layers_covered="IaC-and-app",
            subdirectory=None,
        )
        assert sn.capped is True

    def test_capped_but_cap_type_none_rejected(self) -> None:
        with pytest.raises(ValueError):
            ScopeNote(
                capped=True,
                cap_type="none",
                files_included=5,
                files_seen=20,
                tokens_consumed=500,
                layers_covered="full",
                subdirectory=None,
            )

    def test_uncapped_with_cap_type_rejected(self) -> None:
        with pytest.raises(ValueError):
            ScopeNote(
                capped=False,
                cap_type="file_count",
                files_included=5,
                files_seen=20,
                tokens_consumed=500,
                layers_covered="full",
                subdirectory=None,
            )

    def test_files_included_exceeds_seen_rejected(self) -> None:
        with pytest.raises(ValueError):
            ScopeNote(
                capped=False,
                cap_type="none",
                files_included=11,
                files_seen=10,
                tokens_consumed=0,
                layers_covered="full",
                subdirectory=None,
            )


# ---------------------------------------------------------------------------
# TestRepoInventory
# ---------------------------------------------------------------------------

class TestRepoInventory:
    def _valid_scope_note(self) -> ScopeNote:
        return ScopeNote(
            capped=False,
            cap_type="none",
            files_included=1,
            files_seen=1,
            tokens_consumed=5,
            layers_covered="full",
            subdirectory=None,
        )

    def test_valid_construction(self) -> None:
        sn = self._valid_scope_note()
        repo_ref = _make_repo_ref("/tmp/repo")
        rec = FileRecord(
            path="main.py",
            tier="app",
            estimated_tokens=5,
            content="x = 1",
            actual_tokens=1,
        )
        inv = RepoInventory(
            repo_ref=repo_ref,
            files=[rec],
            scope_note=sn,
            provider="local",
            file_count=1,
        )
        assert inv.file_count == 1

    def test_file_count_mismatch_rejected(self) -> None:
        sn = ScopeNote(
            capped=False,
            cap_type="none",
            files_included=0,
            files_seen=1,
            tokens_consumed=0,
            layers_covered="full",
            subdirectory=None,
        )
        repo_ref = _make_repo_ref("/tmp/repo")
        with pytest.raises(ValueError, match="file_count"):
            RepoInventory(
                repo_ref=repo_ref,
                files=[],  # 0 loaded
                scope_note=sn,
                provider="local",
                file_count=5,  # wrong
            )


# ---------------------------------------------------------------------------
# TestSCMNotConnectedError
# ---------------------------------------------------------------------------

class TestSCMNotConnectedError:
    def test_hint_accessible(self) -> None:
        err = SCMNotConnectedError(hint="Connect via console", provider_attempted="github")
        assert err.hint == "Connect via console"
        assert err.provider_attempted == "github"
        assert str(err) == "Connect via console"

    def test_provider_attempted_optional(self) -> None:
        err = SCMNotConnectedError(hint="Not connected")
        assert err.provider_attempted is None

    def test_is_exception(self) -> None:
        with pytest.raises(SCMNotConnectedError):
            raise SCMNotConnectedError(hint="test")


# ---------------------------------------------------------------------------
# TestClassifyTier — all 8 IaC pattern groups
# ---------------------------------------------------------------------------

class TestClassifyTier:
    # Group 1: Terraform
    def test_tf_extension(self) -> None:
        assert classify_tier("main.tf") == "iac"
        assert classify_tier("modules/vpc/main.tf") == "iac"

    def test_tfvars_extension(self) -> None:
        assert classify_tier("terraform.tfvars") == "iac"
        assert classify_tier("envs/prod.tfvars") == "iac"

    # Group 2: CloudFormation / SAM
    def test_template_yaml(self) -> None:
        assert classify_tier("template.yaml") == "iac"
        assert classify_tier("template.json") == "iac"
        assert classify_tier("sam/template.yaml") == "iac"

    # Group 3: CDK project config
    def test_cdk_json(self) -> None:
        assert classify_tier("cdk.json") == "iac"
        assert classify_tier("cdk.context.json") == "iac"

    # Group 4: CDK stacks
    def test_cdk_stack_py(self) -> None:
        assert classify_tier("infra/vpc_stack.py") == "iac"
        assert classify_tier("stacks/app_stack.ts") == "iac"

    def test_app_py(self) -> None:
        assert classify_tier("app.py") == "iac"
        assert classify_tier("cdk/app.py") == "iac"

    # Group 5: Container config
    def test_dockerfile(self) -> None:
        assert classify_tier("Dockerfile") == "iac"
        assert classify_tier("Dockerfile.dev") == "iac"
        assert classify_tier("docker/Dockerfile.prod") == "iac"

    def test_docker_compose(self) -> None:
        assert classify_tier("docker-compose.yml") == "iac"
        assert classify_tier("docker-compose.override.yaml") == "iac"

    # Group 6: Serverless Framework
    def test_serverless_yml(self) -> None:
        assert classify_tier("serverless.yml") == "iac"
        assert classify_tier("serverless.yaml") == "iac"

    # Group 7/8: IaC / named directories
    def test_infra_yaml(self) -> None:
        assert classify_tier("infra/vpc.yaml") == "iac"
        assert classify_tier("infrastructure/network.yml") == "iac"
        assert classify_tier("deploy/prod.yaml") == "iac"
        assert classify_tier("k8s/deployment.yml") == "iac"
        assert classify_tier("kubernetes/service.yaml") == "iac"

    def test_named_iac_directories(self) -> None:
        assert classify_tier("terraform/modules/vpc.yaml") == "iac"
        assert classify_tier("cloudformation/stacks.yaml") == "iac"
        assert classify_tier("cdk/constructs/bucket.yaml") == "iac"
        assert classify_tier("sam/functions.yaml") == "iac"

    # App-code files — must NOT match IaC
    def test_python_source(self) -> None:
        assert classify_tier("src/agent.py") == "app"
        assert classify_tier("tests/test_agent.py") == "app"

    def test_json_not_iac(self) -> None:
        assert classify_tier("package.json") == "app"
        assert classify_tier("src/config.json") == "app"

    def test_yaml_outside_iac_dir(self) -> None:
        assert classify_tier("config/settings.yaml") == "app"
        assert classify_tier("docs/openapi.yaml") == "app"

    def test_case_insensitive(self) -> None:
        assert classify_tier("DOCKERFILE") == "iac"
        assert classify_tier("Main.TF") == "iac"

    def test_windows_path_separator(self) -> None:
        assert classify_tier("infra\\vpc.yaml") == "iac"
        assert classify_tier("terraform\\modules\\main.tf") == "iac"


# ---------------------------------------------------------------------------
# TestGetScanLimits
# ---------------------------------------------------------------------------

class TestGetScanLimits:
    def test_defaults_when_env_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SCAN_FILE_LIMIT", raising=False)
        monkeypatch.delenv("SCAN_TOKEN_BUDGET", raising=False)
        limits = get_scan_limits()
        assert limits.file_limit == 200   # BLM default
        assert limits.token_budget == 50_000  # BLM default

    def test_reads_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCAN_FILE_LIMIT", "100")
        monkeypatch.setenv("SCAN_TOKEN_BUDGET", "25000")
        limits = get_scan_limits()
        assert limits.file_limit == 100
        assert limits.token_budget == 25_000

    def test_non_integer_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCAN_FILE_LIMIT", "not-a-number")
        monkeypatch.delenv("SCAN_TOKEN_BUDGET", raising=False)
        limits = get_scan_limits()
        assert limits.file_limit == 200

    def test_zero_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCAN_FILE_LIMIT", "0")
        monkeypatch.delenv("SCAN_TOKEN_BUDGET", raising=False)
        limits = get_scan_limits()
        assert limits.file_limit == 200

    def test_negative_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCAN_TOKEN_BUDGET", "-1")
        monkeypatch.delenv("SCAN_FILE_LIMIT", raising=False)
        limits = get_scan_limits()
        assert limits.token_budget == 50_000


# ---------------------------------------------------------------------------
# TestPrioritiseFiles
# ---------------------------------------------------------------------------

class TestPrioritiseFiles:
    def _raw_inventory(self, files: list[FileRecord]) -> RawInventory:
        return RawInventory(
            repo_ref=_make_repo_ref("/tmp/repo"),
            files=files,
            total_count=len(files),
        )

    def test_iac_first(self) -> None:
        files = [
            _make_file_record("src/main.py", "app"),
            _make_file_record("main.tf", "iac"),
        ]
        limits = ScanLimits(file_limit=10, token_budget=100_000)
        result = prioritise_files(self._raw_inventory(files), limits)
        assert result.files[0].path == "main.tf"
        assert result.files[1].path == "src/main.py"

    def test_alphabetical_within_tier(self) -> None:
        files = [
            _make_file_record("z.tf", "iac"),
            _make_file_record("a.tf", "iac"),
            _make_file_record("z.py", "app"),
            _make_file_record("a.py", "app"),
        ]
        limits = ScanLimits(file_limit=10, token_budget=100_000)
        result = prioritise_files(self._raw_inventory(files), limits)
        assert [f.path for f in result.files] == ["a.tf", "z.tf", "a.py", "z.py"]

    def test_file_count_cap_applied(self) -> None:
        # prioritise_files keeps ALL files (BR-RC-008: stubs must remain).
        # truncated_count reports how many are beyond the file cap.
        # Content loading (_load_content) is where the cap is enforced.
        files = [_make_file_record(f"file{i}.py", "app") for i in range(10)]
        limits = ScanLimits(file_limit=5, token_budget=100_000)
        result = prioritise_files(self._raw_inventory(files), limits)
        assert len(result.files) == 10         # all kept as stubs / loaded entries
        assert result.truncated_count == 5     # 5 would be beyond cap
        assert result.app_included == 5        # 5 would get content

    def test_iac_partial_when_cap_within_iac_tier(self) -> None:
        iac_files = [_make_file_record(f"infra{i}.tf", "iac") for i in range(10)]
        app_files = [_make_file_record(f"src{i}.py", "app") for i in range(5)]
        limits = ScanLimits(file_limit=3, token_budget=100_000)
        result = prioritise_files(self._raw_inventory(iac_files + app_files), limits)
        assert result.iac_included == 3
        assert result.app_included == 0
        assert result.truncated_count == 12

    def test_no_cap_when_within_limits(self) -> None:
        files = [_make_file_record(f"f{i}.py", "app") for i in range(5)]
        limits = ScanLimits(file_limit=100, token_budget=100_000)
        result = prioritise_files(self._raw_inventory(files), limits)
        assert result.truncated_count == 0
        assert len(result.files) == 5

    def test_counts_populated(self) -> None:
        files = [
            _make_file_record("a.tf", "iac"),
            _make_file_record("b.tf", "iac"),
            _make_file_record("c.py", "app"),
        ]
        limits = ScanLimits(file_limit=10, token_budget=100_000)
        result = prioritise_files(self._raw_inventory(files), limits)
        assert result.iac_count == 2
        assert result.app_count == 1
        assert result.iac_included == 2
        assert result.app_included == 1


# ---------------------------------------------------------------------------
# TestResolveRepoRef
# ---------------------------------------------------------------------------

class TestResolveRepoRef:
    def test_local_path(self) -> None:
        ref = _resolve_repo_ref("/home/user/repo", None)
        assert ref.kind == "path"
        assert ref.provider is None
        assert ref.subdirectory is None

    def test_github_url(self) -> None:
        ref = _resolve_repo_ref("https://github.com/org/repo", None)
        assert ref.kind == "url"
        assert ref.provider == "github"

    def test_gitlab_url(self) -> None:
        ref = _resolve_repo_ref("https://gitlab.com/org/repo", None)
        assert ref.kind == "url"
        assert ref.provider == "gitlab"

    def test_git_ssh_url(self) -> None:
        ref = _resolve_repo_ref("git@github.com:org/repo.git", None)
        assert ref.kind == "url"
        assert ref.provider == "github"

    def test_subdirectory_passed_through(self) -> None:
        ref = _resolve_repo_ref("/home/user/repo", "src/lambda")
        assert ref.subdirectory == "src/lambda"

    def test_subdirectory_normalised(self) -> None:
        ref = _resolve_repo_ref("https://github.com/org/repo", "src\\lambda\\")
        assert ref.subdirectory == "src/lambda"


# ---------------------------------------------------------------------------
# TestCheckConnectivity
# ---------------------------------------------------------------------------

class TestCheckConnectivity:
    def test_local_path_exists(self, tmp_path: Path) -> None:
        ref = _resolve_repo_ref(str(tmp_path), None)
        result = check_connectivity(ref)
        assert result.connected is True
        assert result.provider == "local"

    def test_local_path_missing(self) -> None:
        ref = _resolve_repo_ref("/nonexistent/path/xyz", None)
        result = check_connectivity(ref)
        assert result.connected is False
        assert result.hint is not None
        assert "does not exist" in result.hint

    def test_url_with_github_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_testtoken")
        monkeypatch.delenv("AGENTCORE_SCM_TOKEN", raising=False)
        ref = _resolve_repo_ref("https://github.com/org/repo", None)
        result = check_connectivity(ref)
        assert result.connected is True
        assert result.provider == "github"

    def test_url_with_agentcore_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTCORE_SCM_TOKEN", "agentcore-tok-123")
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        ref = _resolve_repo_ref("https://github.com/org/repo", None)
        result = check_connectivity(ref)
        assert result.connected is True

    def test_url_without_token_not_connected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AGENTCORE_SCM_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.delenv("BITBUCKET_TOKEN", raising=False)
        ref = _resolve_repo_ref("https://github.com/org/repo", None)
        result = check_connectivity(ref)
        assert result.connected is False
        assert result.hint is not None
        assert "AgentCore" in result.hint


# ---------------------------------------------------------------------------
# TestCloneOrReadRepoLocal — full integration using local tmp directories
# ---------------------------------------------------------------------------

class TestCloneOrReadRepoLocal:
    def _create_repo(self, tmp_path: Path, files: dict[str, str]) -> Path:
        """Create a fake repo with the given relative path → content mapping."""
        for rel_path, content in files.items():
            target = tmp_path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return tmp_path

    def test_basic_scan_populates_inventory(self, tmp_path: Path) -> None:
        repo = self._create_repo(
            tmp_path,
            {
                "main.tf": 'resource "aws_s3_bucket" "b" {}',
                "src/app.py": "import boto3",
            },
        )
        inventory = _clone_or_read_repo_impl(str(repo))
        assert inventory.provider == "local"
        assert inventory.file_count == 2
        assert inventory.scope_note is not None
        assert inventory.scope_note.capped is False
        assert inventory.scope_note.cap_type == "none"

    def test_iac_files_appear_first(self, tmp_path: Path) -> None:
        repo = self._create_repo(
            tmp_path,
            {
                "src/handler.py": "def handler(): pass",
                "infra/vpc.yaml": "AWSTemplateFormatVersion: '2010-09-09'",
                "main.tf": 'provider "aws" {}',
            },
        )
        inventory = _clone_or_read_repo_impl(str(repo))
        loaded = [f for f in inventory.files if f.content is not None]
        iac_indices = [i for i, f in enumerate(loaded) if f.tier == "iac"]
        app_indices = [i for i, f in enumerate(loaded) if f.tier == "app"]
        assert all(i < min(app_indices) for i in iac_indices), (
            "All IaC files must come before all app files"
        )

    def test_scope_note_full_when_no_cap(self, tmp_path: Path) -> None:
        repo = self._create_repo(tmp_path, {"a.py": "x=1", "b.py": "y=2"})
        inventory = _clone_or_read_repo_impl(str(repo))
        assert inventory.scope_note.layers_covered == "full"
        assert inventory.scope_note.files_included == inventory.scope_note.files_seen

    def test_file_count_cap_enforced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAN_FILE_LIMIT", "2")
        monkeypatch.setenv("SCAN_TOKEN_BUDGET", "100000")
        repo = self._create_repo(
            tmp_path,
            {f"file{i}.py": f"x={i}" for i in range(5)},
        )
        inventory = _clone_or_read_repo_impl(str(repo))
        assert inventory.file_count == 2
        assert inventory.scope_note.capped is True
        assert inventory.scope_note.cap_type == "file_count"
        # Stubs must be retained (BR-RC-008)
        assert len(inventory.files) == 5
        stub_count = sum(1 for f in inventory.files if f.content is None)
        assert stub_count == 3

    def test_token_budget_cap_enforced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAN_FILE_LIMIT", "100")
        # Budget of 10 tokens — only the first tiny file fits
        monkeypatch.setenv("SCAN_TOKEN_BUDGET", "10")
        repo = self._create_repo(
            tmp_path,
            {
                "a.tf": "x" * 40,   # ~10 tokens
                "b.tf": "y" * 200,  # ~50 tokens — would exceed budget
            },
        )
        inventory = _clone_or_read_repo_impl(str(repo))
        assert inventory.scope_note.capped is True
        assert inventory.scope_note.cap_type == "token_budget"

    def test_subdirectory_filter_narrows_scope(self, tmp_path: Path) -> None:
        repo = self._create_repo(
            tmp_path,
            {
                "top_level.py": "# top",
                "subdir/nested.py": "# nested",
                "subdir/deeper.py": "# deeper",
            },
        )
        inventory = _clone_or_read_repo_impl(str(repo), subdirectory="subdir")
        paths = [f.path for f in inventory.files if f.content is not None]
        assert all(p.startswith("subdir/") for p in paths)
        assert "top_level.py" not in paths

    def test_scope_note_tokens_consumed_is_actual_not_estimated(
        self, tmp_path: Path
    ) -> None:
        content = "a" * 400  # exactly 100 actual tokens (400 chars / 4)
        repo = self._create_repo(tmp_path, {"test.py": content})
        inventory = _clone_or_read_repo_impl(str(repo))
        assert inventory.scope_note.tokens_consumed == 100

    def test_scope_note_always_present(self, tmp_path: Path) -> None:
        repo = self._create_repo(tmp_path, {"main.py": "pass"})
        inventory = _clone_or_read_repo_impl(str(repo))
        assert inventory.scope_note is not None


# ---------------------------------------------------------------------------
# TestCloneOrReadRepoConnectivity
# ---------------------------------------------------------------------------

class TestCloneOrReadRepoConnectivity:
    def test_raises_scm_not_connected_for_url_without_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AGENTCORE_SCM_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.delenv("BITBUCKET_TOKEN", raising=False)
        with pytest.raises(SCMNotConnectedError) as exc_info:
            _clone_or_read_repo_impl("https://github.com/org/repo")
        assert exc_info.value.hint
        assert exc_info.value.provider_attempted == "github"

    def test_raises_scm_not_connected_for_missing_local_path(self) -> None:
        with pytest.raises(SCMNotConnectedError):
            _clone_or_read_repo_impl("/nonexistent/repo/path/xyz")

    def test_scm_error_is_exception_subclass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AGENTCORE_SCM_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.delenv("BITBUCKET_TOKEN", raising=False)
        with pytest.raises(Exception):
            _clone_or_read_repo_impl("https://github.com/org/repo")

    def test_hint_is_non_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AGENTCORE_SCM_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.delenv("BITBUCKET_TOKEN", raising=False)
        try:
            _clone_or_read_repo_impl("https://github.com/org/repo")
        except SCMNotConnectedError as exc:
            assert isinstance(exc.hint, str)
            assert len(exc.hint) > 0
