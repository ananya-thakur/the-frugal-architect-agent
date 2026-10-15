"""Shared fixtures for agent-assembly tests."""

from __future__ import annotations

import pytest

from agent.tools.repo_connector.types import (
    FileRecord,
    RepoInventory,
    RepoRef,
    ScopeNote,
)


def _make_file_record(
    path: str,
    tier: str = "app",
    content: str | None = "# content",
) -> FileRecord:
    actual = len(content) // 4 if content is not None else None
    return FileRecord(
        path=path,
        tier=tier,  # type: ignore[arg-type]
        estimated_tokens=actual or 0,
        content=content,
        actual_tokens=actual,
    )


def _make_repo_inventory(
    records: list[FileRecord],
    raw: str = "/tmp/myrepo",
    capped: bool = False,
) -> RepoInventory:
    scope = ScopeNote(
        capped=capped,
        cap_type="none" if not capped else "file_count",
        files_included=sum(1 for r in records if r.content is not None),
        files_seen=len(records),
        tokens_consumed=sum(r.estimated_tokens for r in records),
        layers_covered="full",
        subdirectory=None,
    )
    loaded = sum(1 for r in records if r.content is not None)
    return RepoInventory(
        repo_ref=RepoRef(raw=raw, kind="path", provider=None, subdirectory=None),
        files=records,
        scope_note=scope,
        provider="local",
        file_count=loaded,
    )


@pytest.fixture()
def sample_inventory_records() -> list[FileRecord]:
    """Mixed IaC + app records with and without content."""
    return [
        _make_file_record("infra/cdk_stack.py", tier="iac", content="import aws_cdk"),
        _make_file_record("infra/template.yaml", tier="iac", content="AWSTemplateFormatVersion: '2010-09-09'"),
        _make_file_record("main.tf", tier="iac", content="resource 'aws_lambda_function' 'fn' {}"),
        _make_file_record("serverless.yml", tier="iac", content="service: my-svc"),
        _make_file_record("Dockerfile", tier="iac", content="FROM python:3.12"),
        _make_file_record("src/app.py", tier="app", content="import boto3\ndef handler(): pass"),
        _make_file_record("src/models.py", tier="app", content="class User: pass"),
        _make_file_record("stub_beyond_cap.py", tier="app", content=None),  # stub
    ]


@pytest.fixture()
def sample_repo_inventory(sample_inventory_records) -> RepoInventory:
    return _make_repo_inventory(sample_inventory_records)
