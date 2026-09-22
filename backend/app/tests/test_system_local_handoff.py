from pathlib import Path
import subprocess

from app.services import system_update_service as service


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout.strip()


def test_local_handoff_policy_blocks_business_data():
    assert service._local_handoff_file_policy("frontend/src/app/demo.tsx", tracked=False) == (True, "")
    assert service._local_handoff_file_policy("data/tax/raw.xlsx", tracked=False)[0] is False
    assert service._local_handoff_file_policy("税局原始.xlsx", tracked=False)[0] is False
    assert service._local_handoff_file_policy(".env.local", tracked=True)[0] is False
    assert service._local_handoff_file_policy("deploy/mac/runtime.env", tracked=True)[0] is False
    assert service._local_handoff_file_policy("random.bin", tracked=False)[0] is False


def test_upload_local_changes_preserves_worktree_and_excludes_spreadsheet(monkeypatch, tmp_path: Path):
    worktree = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    state_dir = tmp_path / "state"
    worktree.mkdir()
    state_dir.mkdir()

    _git(worktree, "init", "--initial-branch=develop")
    _git(worktree, "config", "user.name", "Pytest")
    _git(worktree, "config", "user.email", "pytest@example.com")
    source = worktree / "frontend" / "src" / "app" / "demo.tsx"
    source.parent.mkdir(parents=True)
    source.write_text("export const value = 1;\n", encoding="utf-8")
    (worktree / ".gitignore").write_text("data/\n*.sqlite\n", encoding="utf-8")
    _git(worktree, "add", ".gitignore", "frontend/src/app/demo.tsx")
    _git(worktree, "commit", "-m", "base")

    subprocess.run(["git", "init", "--bare", str(remote)], text=True, capture_output=True, check=True)
    _git(worktree, "remote", "add", "origin", str(remote))
    _git(worktree, "push", "-u", "origin", "develop")

    source.write_text("export const value = 2;\n", encoding="utf-8")
    (worktree / "notes.md").write_text("# local note\n", encoding="utf-8")
    (worktree / "tax-original.xlsx").write_bytes(b"not-a-real-xlsx")

    def fake_state_dir() -> Path:
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir

    monkeypatch.setattr(service, "_repo_root", lambda: worktree)
    monkeypatch.setattr(service, "_state_dir", fake_state_dir)
    monkeypatch.setattr(service.settings, "DEPLOYMENT_MODE", "native")
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
            "branch": "develop",
            "remote": "origin",
        },
    )

    before_branch = _git(worktree, "branch", "--show-current")
    before_status = _git(worktree, "status", "--porcelain", "--untracked-files=all")

    detected = service.local_changes_payload()
    assert detected["dirty"] is True
    assert {item["path"] for item in detected["files"]} == {
        "frontend/src/app/demo.tsx",
        "notes.md",
    }
    assert {item["path"] for item in detected["excludedFiles"]} == {"tax-original.xlsx"}

    uploaded = service.upload_local_changes(actor="pytest")
    assert uploaded["ok"] is True
    assert uploaded["branch"].startswith("local/")
    assert uploaded["excludedCount"] == 1
    assert uploaded["worktreePreserved"] is True

    assert _git(worktree, "branch", "--show-current") == before_branch
    assert _git(worktree, "status", "--porcelain", "--untracked-files=all") == before_status

    remote_commit = _git(remote, "rev-parse", f"refs/heads/{uploaded['branch']}")
    assert remote_commit == uploaded["commitSha"]
    assert _git(worktree, "show", f"{uploaded['commitSha']}:frontend/src/app/demo.tsx") == "export const value = 2;"
    assert _git(worktree, "show", f"{uploaded['commitSha']}:notes.md") == "# local note"

    missing = subprocess.run(
        ["git", "show", f"{uploaded['commitSha']}:tax-original.xlsx"],
        cwd=str(worktree),
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing.returncode != 0


def test_sync_local_changes_pushes_develop_and_excludes_spreadsheet(monkeypatch, tmp_path: Path):
    worktree = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    state_dir = tmp_path / "state"
    worktree.mkdir()
    state_dir.mkdir()

    _git(worktree, "init", "--initial-branch=develop")
    _git(worktree, "config", "user.name", "Pytest")
    _git(worktree, "config", "user.email", "pytest@example.com")
    source = worktree / "frontend" / "src" / "app" / "demo.tsx"
    source.parent.mkdir(parents=True)
    source.write_text("export const value = 1;\n", encoding="utf-8")
    _git(worktree, "add", "frontend/src/app/demo.tsx")
    _git(worktree, "commit", "-m", "base")

    subprocess.run(["git", "init", "--bare", str(remote)], text=True, capture_output=True, check=True)
    _git(worktree, "remote", "add", "origin", str(remote))
    _git(worktree, "push", "-u", "origin", "develop")

    source.write_text("export const value = 2;\n", encoding="utf-8")
    (worktree / "tax-original.xlsx").write_bytes(b"not-a-real-xlsx")

    def fake_state_dir() -> Path:
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir

    monkeypatch.setattr(service, "_repo_root", lambda: worktree)
    monkeypatch.setattr(service, "_state_dir", fake_state_dir)
    monkeypatch.setattr(service.settings, "DEPLOYMENT_MODE", "native")
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
            "branch": "develop",
            "remote": "origin",
        },
    )

    synced = service.sync_local_changes(actor="pytest")
    assert synced["ok"] is True
    assert synced["branch"] == "develop"
    assert synced["fileCount"] == 1
    assert synced["excludedCount"] == 1
    assert _git(worktree, "branch", "--show-current") == "develop"

    remote_commit = _git(remote, "rev-parse", "refs/heads/develop")
    assert remote_commit == synced["commitSha"]
    assert _git(worktree, "rev-parse", "HEAD") == remote_commit
    assert _git(worktree, "show", f"{remote_commit}:frontend/src/app/demo.tsx") == "export const value = 2;"

    missing = subprocess.run(
        ["git", "show", f"{remote_commit}:tax-original.xlsx"],
        cwd=str(worktree),
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing.returncode != 0
    assert _git(worktree, "status", "--porcelain", "--untracked-files=all") == "?? tax-original.xlsx"
