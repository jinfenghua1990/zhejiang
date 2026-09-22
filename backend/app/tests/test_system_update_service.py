from pathlib import Path
import json
import os
import subprocess

import pytest

from app.services import system_update_service as service


def test_legacy_repository_remote_detection():
    assert service._is_legacy_repo_remote("git@github.com:jinfenghua1990/ecommerce-dashboard.git") is True
    assert service._is_legacy_repo_remote("https://github.com/jinfenghua1990/ecommerce-dashboard") is True
    assert service._is_legacy_repo_remote("https://github.com/jinfenghua1990/ecommerce-workspace.git") is False


def test_update_settings_validation_and_cross_midnight_window():
    cfg = service._validate_settings({
        "enabled": True,
        "mode": "auto_update",
        "checkIntervalMinutes": 10,
        "autoUpdateHour": 23,
        "autoUpdateWindowMinutes": 120,
    })
    assert cfg["mode"] == "auto_update"
    assert service._minute_in_window(23 * 60 + 30, 23 * 60, 120) is True
    assert service._minute_in_window(30, 23 * 60, 120) is True
    assert service._minute_in_window(120, 23 * 60, 120) is False

    with pytest.raises(ValueError):
        service._validate_settings({**cfg, "mode": "unknown"})
    with pytest.raises(ValueError):
        service._validate_settings({**cfg, "checkIntervalMinutes": 1})


def test_check_for_updates_returns_complete_status(monkeypatch, tmp_path: Path):
    current = "1" * 40
    latest = "2" * 40
    status_file = tmp_path / "status.json"
    settings_file = tmp_path / "settings.json"
    history_file = tmp_path / "history.jsonl"

    monkeypatch.setattr(service, "_status_path", lambda: status_file)
    monkeypatch.setattr(service, "_settings_path", lambda: settings_file)
    monkeypatch.setattr(service, "_history_path", lambda: history_file)
    monkeypatch.setattr(service, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        service,
        "load_update_settings",
        lambda: {
            "enabled": True,
            "mode": "auto_download",
            "checkIntervalMinutes": 10,
            "autoUpdateHour": 3,
            "autoUpdateWindowMinutes": 60,
            "branch": service.settings.SYSTEM_UPDATE_BRANCH,
            "remote": service.settings.SYSTEM_UPDATE_REMOTE,
        },
    )

    def fake_git(*args: str, timeout: int = 60) -> str:
        if args == ("rev-parse", "HEAD"):
            return current
        if args == ("branch", "--show-current"):
            return service.settings.SYSTEM_UPDATE_BRANCH
        if args == ("status", "--porcelain"):
            return ""
        if args[:2] == ("fetch", "--quiet"):
            return ""
        if args == ("rev-parse", "FETCH_HEAD"):
            return latest
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(service, "_git", fake_git)
    monkeypatch.setattr(
        service,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        service,
        "_commit_info",
        lambda sha: {"sha": sha, "shortSha": sha[:10], "subject": f"commit-{sha[:4]}", "committedAt": "2026-09-18T00:00:00+08:00"},
    )
    monkeypatch.setattr(
        service,
        "_changes",
        lambda old, new: [{"sha": new, "shortSha": new[:10], "subject": "new", "committedAt": "2026-09-18T00:00:00+08:00"}],
    )

    result = service.check_for_updates(actor="pytest")
    assert result["updateAvailable"] is True
    assert result["currentSha"] == current
    assert result["latestSha"] == latest
    assert result["settings"]["mode"] == "auto_download"
    assert result["changes"][0]["subject"] == "new"


def test_check_for_updates_rejects_legacy_repository_remote(monkeypatch, tmp_path: Path):
    current = "3" * 40
    status_file = tmp_path / "status.json"
    history_file = tmp_path / "history.jsonl"

    monkeypatch.setattr(service, "_status_path", lambda: status_file)
    monkeypatch.setattr(service, "_history_path", lambda: history_file)
    monkeypatch.setattr(service, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        service,
        "load_update_settings",
        lambda: {
            "enabled": True,
            "mode": "manual",
            "checkIntervalMinutes": 10,
            "autoUpdateHour": 3,
            "autoUpdateWindowMinutes": 60,
            "autoInstallLevel": "patch",
            "branch": service.settings.SYSTEM_UPDATE_BRANCH,
            "remote": service.settings.SYSTEM_UPDATE_REMOTE,
        },
    )

    def fake_git(*args: str, timeout: int = 60) -> str:
        if args == ("rev-parse", "HEAD"):
            return current
        if args == ("branch", "--show-current"):
            return service.settings.SYSTEM_UPDATE_BRANCH
        if args == ("status", "--porcelain"):
            return ""
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(service, "_git", fake_git)
    monkeypatch.setattr(
        service,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="git@github.com:jinfenghua1990/ecommerce-dashboard.git\n",
            stderr="",
        ),
    )

    result = service.check_for_updates(actor="pytest")
    assert result["updateAvailable"] is False
    assert "旧仓库 ecommerce-dashboard" in result["lastCheckError"]


def test_manual_check_still_works_when_background_service_disabled(monkeypatch, tmp_path: Path):
    current = "3" * 40
    latest = "4" * 40
    status_file = tmp_path / "status.json"
    settings_file = tmp_path / "settings.json"
    history_file = tmp_path / "history.jsonl"

    monkeypatch.setattr(service, "_status_path", lambda: status_file)
    monkeypatch.setattr(service, "_settings_path", lambda: settings_file)
    monkeypatch.setattr(service, "_history_path", lambda: history_file)
    monkeypatch.setattr(service, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        service,
        "load_update_settings",
        lambda: {
            "enabled": False,
            "mode": "manual",
            "checkIntervalMinutes": 10,
            "autoUpdateHour": 3,
            "autoUpdateWindowMinutes": 60,
            "branch": service.settings.SYSTEM_UPDATE_BRANCH,
            "remote": service.settings.SYSTEM_UPDATE_REMOTE,
        },
    )

    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str, timeout: int = 60) -> str:
        calls.append(args)
        if args == ("rev-parse", "HEAD"):
            return current
        if args == ("branch", "--show-current"):
            return service.settings.SYSTEM_UPDATE_BRANCH
        if args == ("status", "--porcelain"):
            return ""
        if args[:2] == ("fetch", "--quiet"):
            return ""
        if args == ("rev-parse", "FETCH_HEAD"):
            return latest
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(service, "_git", fake_git)
    monkeypatch.setattr(
        service,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(service, "_commit_info", lambda sha: {"sha": sha, "shortSha": sha[:10], "subject": "x", "committedAt": "2026-09-19T00:00:00+08:00"})
    monkeypatch.setattr(service, "_changes", lambda old, new: [])

    result = service.check_for_updates(actor="pytest", automatic=False)
    assert result["updateAvailable"] is True
    assert any(call[:2] == ("fetch", "--quiet") for call in calls)


def test_start_update_stops_when_readiness_is_blocked(monkeypatch):
    monkeypatch.setattr(
        service,
        "update_readiness",
        lambda: {
            "ready": False,
            "blockingCount": 1,
            "warningCount": 0,
            "checks": [
                {
                    "key": "branch",
                    "label": "当前分支",
                    "status": "error",
                    "detail": "wrong branch",
                    "blocking": True,
                }
            ],
        },
    )
    with pytest.raises(ValueError, match="更新环境未就绪"):
        service.start_update(actor="pytest")

def test_check_preserves_last_install_result(monkeypatch, tmp_path: Path):
    current = "5" * 40
    status_file = tmp_path / "status.json"
    history_file = tmp_path / "history.jsonl"

    monkeypatch.setattr(service, "_status_path", lambda: status_file)
    monkeypatch.setattr(service, "_history_path", lambda: history_file)
    monkeypatch.setattr(service, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        service,
        "load_update_settings",
        lambda: {
            "enabled": True,
            "mode": "auto_download",
            "checkIntervalMinutes": 10,
            "autoUpdateHour": 3,
            "autoUpdateWindowMinutes": 60,
            "branch": service.settings.SYSTEM_UPDATE_BRANCH,
            "remote": service.settings.SYSTEM_UPDATE_REMOTE,
        },
    )
    service._atomic_json(
        status_file,
        {
            "phase": "failed",
            "error": "旧安装失败错误",
            "lastAutoError": "旧自动更新错误",
            "lastAutoErrorAt": "2026-09-19T00:30:00+08:00",
            "rollbackErrors": ["旧回滚错误"],
            "lastInstallResult": "success",
            "lastInstallAt": "2026-09-19T01:00:00+08:00",
            "lastInstallFromSha": "a" * 40,
            "lastInstallToSha": "b" * 40,
        },
    )

    def fake_git(*args: str, timeout: int = 60) -> str:
        if args == ("rev-parse", "HEAD"):
            return current
        if args == ("branch", "--show-current"):
            return service.settings.SYSTEM_UPDATE_BRANCH
        if args == ("status", "--porcelain"):
            return ""
        if args[:2] == ("fetch", "--quiet"):
            return ""
        if args == ("rev-parse", "FETCH_HEAD"):
            return current
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(service, "_git", fake_git)
    monkeypatch.setattr(
        service,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        service,
        "_commit_info",
        lambda sha: {
            "sha": sha,
            "shortSha": sha[:10],
            "subject": "same",
            "committedAt": "2026-09-19T01:00:00+08:00",
        },
    )

    result = service.check_for_updates(actor="pytest")

    assert result["lastInstallResult"] == "success"
    assert result["lastInstallAt"] == "2026-09-19T01:00:00+08:00"
    assert result["lastInstallFromSha"] == "a" * 40
    assert result["lastInstallToSha"] == "b" * 40
    assert result["phase"] == "idle"
    assert result["error"] == ""
    assert result["lastAutoError"] == ""
    assert result["lastAutoErrorAt"] == ""
    assert result["rollbackErrors"] == []



def test_load_update_settings_falls_back_when_stored_file_is_invalid(monkeypatch, tmp_path: Path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        '{"mode":"broken","checkIntervalMinutes":1}',
        encoding="utf-8",
    )
    monkeypatch.setattr(service, "_settings_path", lambda: settings_file)

    result = service.load_update_settings()

    assert result["mode"] == "auto_download"
    assert result["checkIntervalMinutes"] == 10


def test_save_update_settings_rejects_changes_during_active_update(monkeypatch, tmp_path: Path):
    status_file = tmp_path / "status.json"
    settings_file = tmp_path / "settings.json"
    status_file.write_text('{"phase":"migrating","pid":12345}', encoding="utf-8")

    monkeypatch.setattr(service, "_status_path", lambda: status_file)
    monkeypatch.setattr(service, "_settings_path", lambda: settings_file)
    monkeypatch.setattr(service, "_pid_running", lambda pid: pid == 12345)

    with pytest.raises(ValueError, match="系统更新正在执行"):
        service.save_update_settings({"mode": "manual"})


def test_git_merge_base_operational_error_is_not_treated_as_divergence(monkeypatch, tmp_path: Path):
    current = "6" * 40
    latest = "7" * 40
    status_file = tmp_path / "status.json"
    history_file = tmp_path / "history.jsonl"

    monkeypatch.setattr(service, "_status_path", lambda: status_file)
    monkeypatch.setattr(service, "_history_path", lambda: history_file)
    monkeypatch.setattr(service, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        service,
        "load_update_settings",
        lambda: {
            "enabled": True,
            "mode": "manual",
            "checkIntervalMinutes": 10,
            "autoUpdateHour": 3,
            "autoUpdateWindowMinutes": 60,
            "branch": service.settings.SYSTEM_UPDATE_BRANCH,
            "remote": service.settings.SYSTEM_UPDATE_REMOTE,
        },
    )

    def fake_git(*args: str, timeout: int = 60) -> str:
        if args == ("rev-parse", "HEAD"):
            return current
        if args == ("branch", "--show-current"):
            return service.settings.SYSTEM_UPDATE_BRANCH
        if args == ("status", "--porcelain"):
            return ""
        if args[:2] == ("fetch", "--quiet"):
            return ""
        if args == ("rev-parse", "FETCH_HEAD"):
            return latest
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(service, "_git", fake_git)
    monkeypatch.setattr(
        service,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=128,
            stdout="",
            stderr="fatal: repository transport error",
        ),
    )

    result = service.check_for_updates(actor="pytest")

    assert result["updateAvailable"] is False
    assert result["diverged"] is False
    assert "repository transport error" in result["lastCheckError"]
    assert result["changes"] == []


def test_changed_files_falls_back_to_commit_range_when_diff_is_empty(monkeypatch):
    current = "a" * 40
    latest = "b" * 40
    calls: list[tuple[str, ...]] = []

    def fake_run(args, **kwargs):
        calls.append(tuple(args))
        if args[:4] == ["git", "diff", "--name-only", "--no-renames"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        if args[:4] == ["git", "log", "--format=", "--name-only"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="frontend/src/app/settings/update/page.tsx\n"
                "backend/app/services/system_update_service.py\n"
                "frontend/src/app/settings/update/page.tsx\n",
                stderr="",
            )
        raise AssertionError(f"unexpected call: {args}")

    monkeypatch.setattr(service, "_run", fake_run)

    result = service._changed_files(current, latest)

    assert result == [
        "frontend/src/app/settings/update/page.tsx",
        "backend/app/services/system_update_service.py",
    ]
    assert any(call[:4] == ("git", "log", "--format=", "--name-only") for call in calls)


def test_module_mapping_covers_ten_navigation_centers():
    cases = {
        "frontend/src/app/page.tsx": "经营中心",
        "frontend/src/app/sales/page.tsx": "销售中心",
        "frontend/src/app/products/page.tsx": "基础货品",
        "frontend/src/app/inventory/page.tsx": "库存中心",
        "frontend/src/app/supply-chain/page.tsx": "供应链中心",
        "frontend/src/app/finance/page.tsx": "财务中心",
        "frontend/src/app/logistics/workbench/page.tsx": "快递物流",
        "frontend/src/app/foreign-trade/page.tsx": "外贸中心",
        "frontend/src/app/exceptions/page.tsx": "异常中心",
        "frontend/src/app/settings/update/page.tsx": "系统设置",
    }
    for path, expected in cases.items():
        assert expected in service._modules_for_paths([path])


def test_module_versions_marks_changed_center(monkeypatch):
    current_sha = "a" * 40
    latest_sha = "b" * 40

    def fake_module_commit_at(sha, patterns):
        label = next(
            label
            for _key, label, rule_patterns in service._MODULE_VERSION_RULES
            if rule_patterns == patterns
        )
        changed = label == "财务中心"
        effective_sha = latest_sha if changed and sha == latest_sha else current_sha
        return {
            "sha": effective_sha,
            "shortSha": effective_sha[:10],
            "subject": f"{label} change",
            "committedAt": "2026-09-21T09:25:00+08:00",
            "version": "2026.09.21.0925",
        }

    monkeypatch.setattr(service, "_module_commit_at", fake_module_commit_at)
    rows = service._module_versions(current_sha, latest_sha)
    states = {row["label"]: row["status"] for row in rows}

    assert len(rows) == 10
    assert states["财务中心"] == "update"
    assert all(
        state == "latest"
        for label, state in states.items()
        if label != "财务中心"
    )


def test_update_classification_detects_patch_module_and_auto_policy():
    result = service._classify_update(
        [{"subject": "fix: 修复月结页面", "sha": "a" * 40}],
        ["frontend/src/app/finance/monthly-send/page.tsx"],
    )
    assert result["updateLevel"] == "patch"
    assert "财务中心" in result["impactedModules"]
    assert result["hasMigration"] is False
    assert service._auto_install_allowed("patch", "patch") is True
    assert service._auto_install_allowed("feature", "patch") is False


def test_update_classification_promotes_migration_to_feature():
    result = service._classify_update(
        [{"subject": "feat: 新增付款明细", "sha": "b" * 40}],
        [
            "backend/alembic/versions/20260920_add_payment_detail.py",
            "frontend/src/app/finance/page.tsx",
        ],
    )
    assert result["updateLevel"] == "feature"
    assert result["hasMigration"] is True
    assert "财务中心" in result["impactedModules"]
    assert "平台公共底层" in result["impactedModules"]
    assert service._auto_install_allowed("feature", "feature") is True


def test_update_classification_marks_core_deployment_change_major():
    result = service._classify_update(
        [{"subject": "chore: 调整启动配置", "sha": "c" * 40}],
        ["backend/app/config.py"],
    )
    assert result["updateLevel"] == "major"
    assert "平台公共底层" in result["impactedModules"]
    assert service._auto_install_allowed("major", "feature") is False


def test_update_settings_accept_auto_install_level():
    cfg = service._validate_settings({
        "enabled": True,
        "mode": "auto_update",
        "checkIntervalMinutes": 10,
        "autoUpdateHour": 3,
        "autoUpdateWindowMinutes": 60,
        "autoInstallLevel": "feature",
    })
    assert cfg["autoInstallLevel"] == "feature"

    with pytest.raises(ValueError, match="自动安装范围"):
        service._validate_settings({**cfg, "autoInstallLevel": "unsafe"})


def test_time_based_version_uses_project_timezone(monkeypatch):
    monkeypatch.setattr(service.settings, "TZ", "Asia/Shanghai")
    assert service._version_from_time("2026-09-20T10:30:00+00:00") == "2026.09.20.1830"
    assert service._version_from_time("2026-09-20T18:30:00+08:00") == "2026.09.20.1830"



def test_production_persistence_policy_blocks_repo_local_storage(monkeypatch, tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()

    monkeypatch.setattr(service.settings, "PERSIST_ROOT", "")
    monkeypatch.setattr(service.settings, "DATA_DIR", str(root / "data"))
    monkeypatch.setattr(service.settings, "BACKUP_DIR", "")
    monkeypatch.setattr(service.settings, "LOG_DIR", "")
    monkeypatch.setattr(service.settings, "DATABASE_URL", "sqlite:///local.db")

    result = service._production_persistence_policy(root)

    assert result["ok"] is False
    assert "PERSIST_ROOT 未配置" in result["issues"]
    assert any("DATA_DIR 位于代码目录内" in item for item in result["issues"])
    assert any("BACKUP_DIR 位于代码目录内" in item for item in result["issues"])
    assert any("LOG_DIR 位于代码目录内" in item for item in result["issues"])
    assert "DATABASE_URL 不是独立 PostgreSQL 数据库" in result["issues"]


def test_production_persistence_policy_accepts_external_storage(monkeypatch, tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    persist = tmp_path / "persistent" / "production"

    monkeypatch.setattr(service.settings, "PERSIST_ROOT", str(persist))
    monkeypatch.setattr(service.settings, "DATA_DIR", str(persist / "data"))
    monkeypatch.setattr(service.settings, "BACKUP_DIR", str(persist / "backups"))
    monkeypatch.setattr(service.settings, "LOG_DIR", str(persist / "logs"))
    monkeypatch.setattr(
        service.settings,
        "DATABASE_URL",
        "postgresql+psycopg://ecommerce:secret@localhost:5432/ecommerce",
    )

    result = service._production_persistence_policy(root)

    assert result["ok"] is True
    assert result["issues"] == []
    assert result["persistRoot"] == str(persist.resolve())


def test_global_update_lock_rejects_parallel_update(monkeypatch, tmp_path: Path):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setattr(service, "_repo_root", lambda: root)

    service._acquire_update_lock(
        run_id="run-a",
        target_sha="a" * 40,
        actor="pytest",
    )
    owner = json.loads((root / ".git" / "ecommerce-system-update.lock" / "owner.json").read_text())
    assert owner["runId"] == "run-a"
    assert owner["pid"] == os.getpid()

    with pytest.raises(ValueError, match="全局更新锁"):
        service._acquire_update_lock(
            run_id="run-b",
            target_sha="b" * 40,
            actor="pytest",
        )

    service._release_update_lock(run_id="run-a")
    assert not (root / ".git" / "ecommerce-system-update.lock").exists()


def test_reference_transaction_hook_blocks_manual_git_and_allows_updater(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[3]
    guard = repo_root / "scripts" / "update-guard.sh"
    worktree = tmp_path / "guard-repo"
    worktree.mkdir()

    subprocess.run(["git", "init", "-q", "-b", "develop"], cwd=worktree, check=True)
    subprocess.run(["git", "config", "user.email", "pytest@example.invalid"], cwd=worktree, check=True)
    subprocess.run(["git", "config", "user.name", "pytest"], cwd=worktree, check=True)
    (worktree / "README").write_text("guard\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=worktree, check=True)

    scripts_dir = worktree / "scripts"
    scripts_dir.mkdir()
    local_guard = scripts_dir / "update-guard.sh"
    local_guard.write_text(guard.read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["bash", str(local_guard), "install", str(worktree)], check=True)

    blocked = subprocess.run(
        ["git", "branch", "manual-blocked", "HEAD"],
        cwd=worktree,
        text=True,
        capture_output=True,
        check=False,
    )
    assert blocked.returncode != 0
    assert "系统更新" in (blocked.stderr + blocked.stdout)

    allowed_env = os.environ.copy()
    allowed_env["ECOMMERCE_ALLOW_MANUAL_GIT"] = "1"
    subprocess.run(
        ["git", "branch", "emergency-allowed", "HEAD"],
        cwd=worktree,
        env=allowed_env,
        check=True,
    )

    lock_dir = worktree / ".git" / "ecommerce-system-update.lock"
    lock_dir.mkdir()
    (lock_dir / "owner.json").write_text(
        json.dumps({"runId": "run-123", "pid": os.getpid(), "targetSha": "c" * 40}),
        encoding="utf-8",
    )

    service_env = os.environ.copy()
    service_env["ECOMMERCE_UPDATE_SERVICE_GIT"] = "1"
    blocked_during_update = subprocess.run(
        ["git", "branch", "service-blocked-during-update", "HEAD"],
        cwd=worktree,
        env=service_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert blocked_during_update.returncode != 0

    updater_env = os.environ.copy()
    updater_env["ECOMMERCE_UPDATE_RUN_ID"] = "run-123"
    subprocess.run(
        ["git", "branch", "updater-allowed", "HEAD"],
        cwd=worktree,
        env=updater_env,
        check=True,
    )



def test_runner_rejects_target_sha_different_from_locked_target(tmp_path: Path):
    """同一个 runId 也不能把服务端锁定的目标 SHA 偷换成另一个 SHA。"""
    from argparse import Namespace
    from importlib.util import module_from_spec, spec_from_file_location

    repo_root = Path(__file__).resolve().parents[3]
    runner_path = repo_root / "scripts" / "system_update_runner.py"
    spec = spec_from_file_location("system_update_runner_test_module", runner_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    fake_root = tmp_path / "repo"
    (fake_root / ".git" / "ecommerce-system-update.lock").mkdir(parents=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    locked_target = "a" * 40
    requested_target = "b" * 40
    owner_file = fake_root / ".git" / "ecommerce-system-update.lock" / "owner.json"
    owner_file.write_text(
        json.dumps({
            "runId": "run-locked",
            "pid": os.getpid(),
            "targetSha": locked_target,
            "actor": "pytest",
        }),
        encoding="utf-8",
    )

    args = Namespace(
        root=str(fake_root),
        data_dir=str(data_dir),
        target=requested_target,
        branch="develop",
        remote="origin",
        actor="pytest",
        run_id="run-locked",
        health_url="http://127.0.0.1:8000/healthz",
    )
    runner = module.Runner(args)

    with pytest.raises(RuntimeError, match="目标版本与全局锁不一致"):
        runner.claim_update_lock()

    owner = json.loads(owner_file.read_text(encoding="utf-8"))
    assert owner["targetSha"] == locked_target

def test_detached_runner_blocks_production_repo_local_persistence(monkeypatch, tmp_path: Path):
    from argparse import Namespace
    from importlib.util import module_from_spec, spec_from_file_location

    repo_root = Path(__file__).resolve().parents[3]
    runner_path = repo_root / "scripts" / "system_update_runner.py"
    spec = spec_from_file_location("system_update_runner_persistence_test_module", runner_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    data_dir = fake_root / "data"
    args = Namespace(
        root=str(fake_root),
        data_dir=str(data_dir),
        target="a" * 40,
        branch="develop",
        remote="origin",
        actor="pytest",
        run_id="run-persist",
        health_url="http://127.0.0.1:8000/healthz",
    )
    runner = module.Runner(args)

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("PERSIST_ROOT", raising=False)

    with pytest.raises(RuntimeError, match="PERSIST_ROOT 未配置"):
        runner.validate_persistence_isolation()


def test_detached_runner_accepts_external_production_persistence(monkeypatch, tmp_path: Path):
    from argparse import Namespace
    from importlib.util import module_from_spec, spec_from_file_location

    repo_root = Path(__file__).resolve().parents[3]
    runner_path = repo_root / "scripts" / "system_update_runner.py"
    spec = spec_from_file_location("system_update_runner_external_persistence_test_module", runner_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    persist = tmp_path / "persistent" / "production"
    data_dir = persist / "data"
    args = Namespace(
        root=str(fake_root),
        data_dir=str(data_dir),
        target="a" * 40,
        branch="develop",
        remote="origin",
        actor="pytest",
        run_id="run-persist-ok",
        health_url="http://127.0.0.1:8000/healthz",
    )
    runner = module.Runner(args)

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PERSIST_ROOT", str(persist))
    monkeypatch.setenv("BACKUP_DIR", str(persist / "backups"))
    monkeypatch.setenv("LOG_DIR", str(persist / "logs"))

    runner.validate_persistence_isolation()



def test_migration_database_policy_warns_when_not_configured(monkeypatch):
    monkeypatch.setattr(
        service.settings,
        "DATABASE_URL",
        "postgresql+psycopg://ecommerce_app:app-secret@localhost:5432/ecommerce",
    )
    monkeypatch.setattr(service.settings, "MIGRATION_DATABASE_URL", "")

    result = service._migration_database_policy()

    assert result["configured"] is False
    assert result["sameTarget"] is True
    assert result["separated"] is False


def test_migration_database_policy_accepts_separate_user_same_database(monkeypatch):
    monkeypatch.setattr(
        service.settings,
        "DATABASE_URL",
        "postgresql+psycopg://ecommerce_app:app-secret@postgres:5432/ecommerce",
    )
    monkeypatch.setattr(
        service.settings,
        "MIGRATION_DATABASE_URL",
        "postgresql+psycopg://ecommerce_migrator:migrate-secret@postgres:5432/ecommerce",
    )

    result = service._migration_database_policy()

    assert result["configured"] is True
    assert result["sameTarget"] is True
    assert result["separated"] is True
    assert result["issues"] == []


def test_migration_database_policy_rejects_different_database(monkeypatch):
    monkeypatch.setattr(
        service.settings,
        "DATABASE_URL",
        "postgresql+psycopg://ecommerce_app:app-secret@postgres:5432/ecommerce",
    )
    monkeypatch.setattr(
        service.settings,
        "MIGRATION_DATABASE_URL",
        "postgresql+psycopg://ecommerce_migrator:migrate-secret@postgres:5432/ecommerce_staging",
    )

    result = service._migration_database_policy()

    assert result["sameTarget"] is False
    assert result["separated"] is False
    assert any("指向不同数据库" in item for item in result["issues"])


def test_detached_runner_uses_migration_url_and_blocks_wrong_database(monkeypatch, tmp_path: Path):
    from argparse import Namespace
    from importlib.util import module_from_spec, spec_from_file_location

    repo_root = Path(__file__).resolve().parents[3]
    runner_path = repo_root / "scripts" / "system_update_runner.py"
    spec = spec_from_file_location("system_update_runner_db_roles_test_module", runner_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    args = Namespace(
        root=str(fake_root),
        data_dir=str(data_dir),
        target="a" * 40,
        branch="develop",
        remote="origin",
        actor="pytest",
        run_id="run-db-role",
        health_url="http://127.0.0.1:8000/healthz",
    )
    runner = module.Runner(args)

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ecommerce_app:app-secret@localhost:5432/ecommerce",
    )
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL",
        "postgresql+psycopg://ecommerce_migrator:migrate-secret@localhost:5432/ecommerce",
    )
    assert runner.migration_env()["DATABASE_URL"].startswith(
        "postgresql+psycopg://ecommerce_migrator:"
    )
    runner.validate_migration_database_target()

    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL",
        "postgresql+psycopg://ecommerce_migrator:migrate-secret@localhost:5432/ecommerce_staging",
    )
    with pytest.raises(RuntimeError, match="指向不同数据库"):
        runner.validate_migration_database_target()
