"""Tests for GitHub sync DB models and service logic."""
import pytest
from sqlalchemy import inspect as sa_inspect
from app.models import GithubSyncConfig, GithubSyncJob, Workflow


def test_github_sync_config_model_exists():
    assert GithubSyncConfig.__tablename__ == "github_sync_configs"


def test_github_sync_job_model_exists():
    assert GithubSyncJob.__tablename__ == "github_sync_jobs"


def test_workflow_has_sync_columns():
    mapper = Workflow.__mapper__
    cols = {c.key for c in mapper.columns}
    assert "github_sync_sha" in cols
    assert "github_sync_status" in cols
    assert "github_sync_conflict_sha" in cols
