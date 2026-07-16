from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from wt.commands import new as new_cmd
from wt.commands import tenant as tenant_cmd
from wt.envfile import read_env
from wt.manifest import EnvPatch, Manifest, TenantConfig
from wt.types import Worktree


def _tenant_project(
    tmp_path: Path, *, package_manifest: dict[str, object]
) -> tuple[SimpleNamespace, Path, Path]:
    search_path = tmp_path / "tenants"
    package = search_path / "kingfisher-prod"
    package.mkdir(parents=True)
    (package / "manifest.yaml").write_text(yaml.safe_dump(package_manifest))
    worktree = tmp_path / "brain-app--data-tab"
    worktree.mkdir()
    (worktree / ".env").write_text(
        "DEPLOYMENT_ROOT=/old/package\nAPP_TENANT=warwick\n"
    )
    manifest = Manifest(
        project="brain-app",
        worktree_prefix="brain-app--",
        services=[],
        env_patches=[
            EnvPatch(file=".env", set={"DEPLOYMENT_ROOT": "{tenant_path}"})
        ],
        tenant=TenantConfig(
            env_var="DEPLOYMENT_ROOT",
            search_paths=[str(search_path)],
            identity_env="APP_TENANT",
            identity_source="name",
        ),
    )
    entry = Worktree(
        shorthand="data-tab",
        path=str(worktree),
        branch="feat/data-tab",
        tenant="old-package",
    )
    project = SimpleNamespace(manifest=manifest, find=lambda shorthand: entry)
    return project, worktree, package


def test_tenant_switch_writes_path_and_manifest_identity_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project, worktree, package = _tenant_project(
        tmp_path, package_manifest={"name": "kfd", "display_name": "Kingfisher"}
    )
    monkeypatch.setattr(tenant_cmd.Project, "discover", lambda start: project)
    monkeypatch.setattr(tenant_cmd, "is_port_bound", lambda port: False)

    result = tenant_cmd.set_run(tmp_path, "data-tab", "kingfisher-prod")

    assert result == 0
    assert read_env(worktree / ".env") == {
        "DEPLOYMENT_ROOT": str(package.resolve()),
        "APP_TENANT": "kfd",
    }
    output = capsys.readouterr().out.replace("\n", "")
    assert f"DEPLOYMENT_ROOT={package.resolve()}" in output
    assert "APP_TENANT=kfd" in output


def test_tenant_switch_missing_identity_warns_and_leaves_identity_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project, worktree, package = _tenant_project(
        tmp_path, package_manifest={"display_name": "Kingfisher"}
    )
    monkeypatch.setattr(tenant_cmd.Project, "discover", lambda start: project)
    monkeypatch.setattr(tenant_cmd, "is_port_bound", lambda port: False)

    result = tenant_cmd.set_run(tmp_path, "data-tab", "kingfisher-prod")

    assert result == 0
    assert read_env(worktree / ".env") == {
        "DEPLOYMENT_ROOT": str(package.resolve()),
        "APP_TENANT": "warwick",
    }
    output = capsys.readouterr()
    assert "WARNING" in output.err
    assert "field 'name' is missing" in output.err
    assert "APP_TENANT was left unchanged" in output.err


def test_new_with_tenant_writes_path_and_manifest_identity(
    repo_factory,
    tmp_path: Path,
) -> None:
    repo = repo_factory()
    tenant_root = tmp_path / "tenants"
    package = tenant_root / "kingfisher-prod"
    package.mkdir(parents=True)
    (package / "manifest.yaml").write_text("name: kfd\n")
    (repo / ".env").write_text(
        "DEPLOYMENT_ROOT=/old/package\nAPP_TENANT=warwick\n"
    )
    (repo / ".wt.yaml").write_text(
        yaml.safe_dump(
            {
                "project": "brain-app",
                "worktree_prefix": "brain-app--",
                "services": [],
                "env_patches": [
                    {
                        "file": ".env",
                        "set": {"DEPLOYMENT_ROOT": "{tenant_path}"},
                    }
                ],
                "tenant": {
                    "env_var": "DEPLOYMENT_ROOT",
                    "search_paths": [str(tenant_root)],
                    "identity_env": "APP_TENANT",
                    "identity_source": "name",
                },
            },
            sort_keys=False,
        )
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "tenant fixture"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    result = new_cmd.run(
        start=repo,
        shorthand="data-tab",
        branch=None,
        tenant="kingfisher-prod",
        skip_migrate=True,
    )

    target = tmp_path / "brain-app--data-tab"
    assert result == 0
    assert read_env(target / ".env") == {
        "DEPLOYMENT_ROOT": str(package.resolve()),
        "APP_TENANT": "kfd",
    }
