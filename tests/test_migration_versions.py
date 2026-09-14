"""Catch migration version collisions in merged trees without requiring PostgreSQL."""

from pathlib import Path

import pytest

from scripts.check_schema_drift import assert_versions_unique, migration_files


def test_repository_migration_versions_are_unique():
    migration_files()


def test_parallel_branch_migrations_cannot_share_a_version():
    with pytest.raises(SystemExit, match="two migrations claim version 039"):
        assert_versions_unique([
            Path("V039__cap_formalization_defect_awards.sql"),
            Path("V039__preserve_legacy_submission_updates.sql"),
        ])
