#!/usr/bin/env python3
"""Detached system update runner.

This file is copied into DATA_DIR/system-update before execution so a git reset cannot
replace the code that is currently performing the update.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


class Runner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.root = Path(args.root).resolve()
        self.data_dir = Path(args.data_dir).resolve()
        self.state_dir = self.data_dir / "system-update"
        self.status_file = self.state_dir / "status.json"
        self.history_file = self.state_dir / "history.jsonl"
        self.maintenance_file = self.state_dir / "maintenance.json"
        self.lock_dir = self.root / ".git" / "ecommerce-system-update.lock"
        self.lock_owner_file = self.lock_dir / "owner.json"
        self.venv_python = self.root / "backend" / ".venv" / "bin" / "python"
        self.pip = self.root / "backend" / ".venv" / "bin" / "pip"
        self.previous_sha = ""
        self.previous_db_revision = ""
        self.changed_files: list[str] = []
        self.git_switched = False
        self.migration_started = False
        self.workers_quiesced = False
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def log(self, message: str) -> None:
        print(f"[{now_iso()}] {message}", flush=True)

    def read_status(self) -> dict[str, Any]:
        try:
            value = json.loads(self.status_file.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def status(self, phase: str, progress: int, message: str, **extra: Any) -> None:
        value = self.read_status()
        value.update({
            "phase": phase,
            "progress": max(0, min(100, progress)),
            "message": message,
            "updatedAt": now_iso(),
            **extra,
        })
        tmp = self.status_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.status_file)
        self.log(f"{phase} {progress}% · {message}")

    def set_maintenance(self, enabled: bool, message: str = "") -> None:
        if enabled:
            payload = {
                "active": True,
                "message": message or "系统正在更新，请稍候",
                "runId": self.read_status().get("runId"),
                "startedAt": now_iso(),
            }
            tmp = self.maintenance_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.maintenance_file)
            self.log("已进入维护模式")
        else:
            try:
                self.maintenance_file.unlink()
            except FileNotFoundError:
                pass
            self.log("已退出维护模式")

    def history(self, result: str, message: str, **extra: Any) -> None:
        row = {
            "at": now_iso(),
            "result": result,
            "message": message,
            "actor": self.args.actor,
            "fromSha": self.previous_sha,
            "toSha": self.args.target,
            **extra,
        }
        with self.history_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def read_lock_owner(self) -> dict[str, Any]:
        try:
            value = json.loads(self.lock_owner_file.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def claim_update_lock(self) -> None:
        if not self.lock_dir.is_dir():
            raise RuntimeError("系统更新全局锁不存在，拒绝无锁执行更新")
        owner = self.read_lock_owner()
        if owner.get("runId") != self.args.run_id:
            raise RuntimeError("系统更新全局锁已由其他任务持有")
        locked_target = str(owner.get("targetSha") or "")
        if locked_target != self.args.target:
            raise RuntimeError("系统更新目标版本与全局锁不一致，拒绝执行")
        owner.update({
            "runId": self.args.run_id,
            "pid": os.getpid(),
            "targetSha": self.args.target,
            "actor": self.args.actor,
            "runnerClaimedAt": now_iso(),
        })
        tmp = self.lock_owner_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(owner, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.lock_owner_file)

    def release_update_lock(self) -> None:
        if not self.lock_dir.exists():
            return
        owner = self.read_lock_owner()
        if owner.get("runId") != self.args.run_id:
            return
        shutil.rmtree(self.lock_dir, ignore_errors=True)

    def run(self, command: list[str], *, cwd: Path | None = None, timeout: int = 1800,
            check: bool = True, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        self.log("$ " + " ".join(command))
        env = os.environ.copy()
        env["ECOMMERCE_UPDATE_RUN_ID"] = self.args.run_id
        if env_overrides:
            env.update(env_overrides)
        result = subprocess.run(
            command,
            cwd=str(cwd or self.root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env=env,
        )
        if result.stdout:
            print(result.stdout.rstrip(), flush=True)
        if check and result.returncode != 0:
            tail = (result.stdout or "").strip()[-3000:]
            raise RuntimeError(f"命令失败（{result.returncode}）：{' '.join(command)}\n{tail}")
        return result

    def git(self, *args: str, timeout: int = 180) -> str:
        return self.run(["git", *args], timeout=timeout).stdout.strip()

    def migration_env(self) -> dict[str, str]:
        migration_url = (os.environ.get("MIGRATION_DATABASE_URL") or "").strip()
        return {"DATABASE_URL": migration_url} if migration_url else {}

    @staticmethod
    def _database_target(url: str) -> tuple[str, int, str]:
        parsed = urlsplit(url)
        return (
            (parsed.hostname or "").lower(),
            parsed.port or 5432,
            (parsed.path or "").lstrip("/"),
        )

    def validate_migration_database_target(self) -> None:
        app_url = (os.environ.get("DATABASE_URL") or "").strip()
        migration_url = (os.environ.get("MIGRATION_DATABASE_URL") or "").strip()
        if not migration_url or not app_url:
            return
        if self._database_target(app_url) != self._database_target(migration_url):
            raise RuntimeError(
                "MIGRATION_DATABASE_URL 与 DATABASE_URL 指向不同数据库，拒绝执行迁移"
            )

    def db_revision(self) -> str:
        result = self.run(
            [str(self.venv_python), "-m", "alembic", "current"],
            cwd=self.root / "backend",
            timeout=120,
            check=False,
            env_overrides=self.migration_env(),
        )
        # Alembic may append "(head)" or other labels; first revision token is enough.
        for line in result.stdout.splitlines():
            match = re.match(r"^([0-9a-f]+)(?:\s|$)", line.strip())
            if match:
                return match.group(1)
        return ""

    def check_health(self, attempts: int = 45, delay: float = 2.0) -> bool:
        urls = [self.args.health_url, "http://127.0.0.1:8000/healthz", "http://localhost:8000/healthz"]
        bind_host = (os.environ.get("API_BIND_HOST") or "").strip()
        if bind_host and bind_host not in {"0.0.0.0", "::"}:
            urls.append(f"http://{bind_host}:8000/healthz")
        urls = list(dict.fromkeys(urls))
        last_error = ""
        for i in range(attempts):
            for url in urls:
                try:
                    with urllib.request.urlopen(url, timeout=3) as response:
                        if response.status == 200:
                            payload = response.read(2048).decode("utf-8", errors="replace")
                            if '"ok":true' in payload.replace(" ", "").lower():
                                self.log(f"健康检查通过（第 {i + 1} 次，{url}）")
                                return True
                except Exception as exc:
                    last_error = f"{url}: {exc}"
            self.log(f"健康检查等待中 {i + 1}/{attempts}: {last_error}")
            time.sleep(delay)
        return False

    @staticmethod
    def _path_inside_repo(path: Path, root: Path) -> bool:
        resolved_path = path.resolve()
        resolved_root = root.resolve()
        return resolved_path == resolved_root or resolved_root in resolved_path.parents

    def _runtime_path(self, raw: str, fallback: Path) -> Path:
        value = (raw or "").strip()
        path = Path(value).expanduser() if value else fallback
        if not path.is_absolute():
            path = self.root / path
        return path.resolve()

    def validate_persistence_isolation(self) -> None:
        """Second-line production guard executed by the detached updater itself."""
        if (os.environ.get("APP_ENV") or "development").strip().lower() != "production":
            return

        persist_raw = (os.environ.get("PERSIST_ROOT") or "").strip()
        if not persist_raw:
            raise RuntimeError(
                "生产环境持久化目录未完全分离：PERSIST_ROOT 未配置；"
                "请先把业务文件、备份和日志迁移到代码目录外；"
                "可在项目根目录执行 make persistence-migrate"
            )

        persist_root = self._runtime_path(persist_raw, self.root)
        backup_dir = self._runtime_path(
            os.environ.get("BACKUP_DIR") or "",
            persist_root / "backups",
        )
        log_dir = self._runtime_path(
            os.environ.get("LOG_DIR") or "",
            persist_root / "logs",
        )
        paths = (
            ("PERSIST_ROOT", persist_root),
            ("DATA_DIR", self.data_dir),
            ("BACKUP_DIR", backup_dir),
            ("LOG_DIR", log_dir),
        )
        violations = [
            f"{label} 位于代码目录内：{path.resolve()}"
            for label, path in paths
            if self._path_inside_repo(path, self.root)
        ]
        if violations:
            raise RuntimeError(
                "生产环境持久化目录未完全分离：" + "；".join(violations)
            )

    def preflight(self) -> None:
        self.status("preflight", 5, "校验本地仓库和目标版本")
        if not SHA_RE.fullmatch(self.args.target):
            raise RuntimeError("目标 commit 格式无效")
        self.validate_persistence_isolation()
        self.validate_migration_database_target()
        if not self.venv_python.is_file():
            raise RuntimeError("backend/.venv 不存在，无法安全执行更新")
        current_branch = self.git("branch", "--show-current")
        if current_branch != self.args.branch:
            raise RuntimeError(
                f"当前分支 {current_branch or '(detached)'} 与更新分支 {self.args.branch} 不一致"
            )
        dirty = self.git("status", "--porcelain")
        if dirty:
            raise RuntimeError("工作区存在未提交修改，自动更新已停止")
        self.previous_sha = self.git("rev-parse", "HEAD")
        self.git("fetch", "--quiet", self.args.remote, self.args.branch)
        remote_sha = self.git("rev-parse", "FETCH_HEAD")
        if remote_sha != self.args.target:
            raise RuntimeError("远端分支已产生更新，本次目标版本过期，请重新检查后再更新")
        ancestor = self.run(
            ["git", "merge-base", "--is-ancestor", self.previous_sha, self.args.target],
            timeout=30,
            check=False,
        )
        if ancestor.returncode != 0:
            raise RuntimeError("远端版本不是当前版本的快进后继，禁止自动覆盖")
        self.changed_files = [
            line.strip() for line in self.git(
                "diff", "--name-only", self.previous_sha, self.args.target
            ).splitlines() if line.strip()
        ]
        self.previous_db_revision = self.db_revision()
        self.status(
            "preflight", 10, "预检查通过",
            previousSha=self.previous_sha,
            targetSha=self.args.target,
            previousDbRevision=self.previous_db_revision,
            changedFileCount=len(self.changed_files),
        )

    def configured_backup_dir(self) -> Path:
        """Resolve the same backup directory contract used by scripts/backup.sh."""
        raw = (os.environ.get("BACKUP_DIR") or "").strip()
        if raw:
            path = Path(raw).expanduser()
        else:
            persist_raw = (os.environ.get("PERSIST_ROOT") or "").strip()
            path = Path(persist_raw).expanduser() / "backups" if persist_raw else self.root / "backups"
        if not path.is_absolute():
            path = self.root / path
        return path.resolve()

    @staticmethod
    def read_backup_manifest(path: Path) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
        return values

    def backup(self) -> None:
        self.status("backup", 20, "更新前备份数据库和业务文件")
        started = time.time()
        result = self.run([str(self.root / "scripts" / "backup.sh")], timeout=3600)

        # 新版 backup.sh 会直接返回本次完整恢复点的 manifest。
        # 更新器自身先于工作树切换，因此首次升级到新版时仍要兼容旧 backup.sh。
        manifest_path: Path | None = None
        for line in (result.stdout or "").splitlines():
            if line.startswith("BACKUP_MANIFEST="):
                raw = line.split("=", 1)[1].strip()
                if raw:
                    manifest_path = Path(raw).expanduser().resolve()
                break

        if manifest_path is not None and manifest_path.is_file():
            values = self.read_backup_manifest(manifest_path)
            backups = manifest_path.parent
            db_name = values.get("db", "")
            data_name = values.get("data", "")
            if not db_name or Path(db_name).name != db_name:
                raise RuntimeError("备份 manifest 缺少有效数据库文件名")
            db_path = backups / db_name
            data_path = backups / data_name if data_name else None
            if not db_path.is_file() or db_path.stat().st_size <= 0:
                raise RuntimeError("备份 manifest 指向的数据库备份不存在或为空")
            if data_path is not None and (not data_path.is_file() or data_path.stat().st_size <= 0):
                raise RuntimeError("备份 manifest 指向的 data 归档不存在或为空")
            self.status(
                "backup", 27, "备份完成",
                backupManifest=str(manifest_path),
                backupDb=str(db_path),
                backupData=str(data_path) if data_path else "",
            )
            return

        # 兼容旧版脚本：按 PERSIST_ROOT/BACKUP_DIR 找本次刚生成的文件，
        # 不能再固定读取仓库 ./backups，否则数据/软件分离部署会误判备份失败。
        backups = self.configured_backup_dir()
        db_backups = sorted(
            (p for p in backups.glob("db_*.dump") if p.stat().st_mtime >= started - 2 and p.stat().st_size > 0),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        data_backups = sorted(
            (p for p in backups.glob("data_*.tar.gz") if p.stat().st_mtime >= started - 2 and p.stat().st_size > 0),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not db_backups:
            raise RuntimeError(
                f"备份脚本已结束，但未在 {backups} 发现本次新生成的有效数据库备份"
            )
        self.status(
            "backup", 27, "备份完成",
            backupDb=str(db_backups[0]),
            backupData=str(data_backups[0]) if data_backups else "",
        )

    def quiesce_workers(self) -> None:
        self.status("quiescing", 16, "暂停 worker / beat，冻结后台写入后再生成恢复点")
        helper = self.root / "scripts" / "quiesce-update-workers.sh"
        self.run(["bash", str(helper)], timeout=120)
        self.workers_quiesced = True

    def switch_code(self) -> None:
        self.status("installing", 30, "切换到已下载的新版本")
        self.git("reset", "--hard", self.args.target)
        self.git_switched = True

    def install_dependencies(self) -> None:
        backend_dep_changed = "backend/requirements.txt" in self.changed_files
        frontend_dep_changed = any(
            path in self.changed_files
            for path in ("frontend/package.json", "frontend/package-lock.json")
        )
        if backend_dep_changed:
            self.status("installing", 38, "更新后端依赖")
            self.run([str(self.pip), "install", "-r", "requirements.txt"], cwd=self.root / "backend", timeout=3600)
        if frontend_dep_changed or not (self.root / "frontend" / "node_modules").is_dir():
            self.status("installing", 44, "更新前端依赖")
            command = ["npm", "ci"] if (self.root / "frontend" / "package-lock.json").is_file() else ["npm", "install"]
            self.run(command, cwd=self.root / "frontend", timeout=3600)

    def migrate(self) -> None:
        self.status("migrating", 52, "执行数据库迁移")
        actual_sha = self.git("rev-parse", "HEAD")
        if actual_sha != self.args.target:
            raise RuntimeError(
                f"执行迁移前 HEAD 已偏离目标版本：{actual_sha} != {self.args.target}"
            )
        migration_file = self.root / "backend" / "alembic" / "versions" / "drift20260920_align_supplier_nullable.py"
        migration_marker = "missing"
        if migration_file.is_file():
            migration_marker = hashlib.sha256(migration_file.read_bytes()).hexdigest()[:16]
        self.log(
            f"迁移代码确认：HEAD={actual_sha} drift20260920.sha256={migration_marker}"
        )
        self.migration_started = True
        self.run(
            [str(self.venv_python), "-m", "alembic", "upgrade", "head"],
            cwd=self.root / "backend",
            timeout=1800,
            env_overrides=self.migration_env(),
        )

    def build_frontend(self) -> None:
        frontend_changed = any(path.startswith("frontend/") for path in self.changed_files)
        if frontend_changed or not (self.root / "frontend" / "out" / "index.html").is_file():
            self.status("building", 65, "构建新版前端")
            self.run(["npm", "run", "build"], cwd=self.root / "frontend", timeout=3600)
            if not (self.root / "frontend" / "out" / "index.html").is_file():
                raise RuntimeError("前端构建完成但未生成 frontend/out/index.html")
        else:
            self.status("building", 65, "本次无前端改动，跳过前端构建")

    def restart(self) -> None:
        self.status("restarting", 78, "重新启动 API / worker / beat")
        self.run(["make", "restart"], timeout=120)
        self.status("healthcheck", 84, "等待新版本健康检查")
        if not self.check_health():
            raise RuntimeError("新版本重启后健康检查超时")

    def rollback(self, cause: str) -> bool:
        self.status("rollback", 88, "更新失败，正在自动回滚", rollbackCause=cause)
        rollback_errors: list[str] = []
        try:
            if self.migration_started and self.previous_db_revision:
                # 必须在 reset 回旧代码之前执行：新迁移脚本的 downgrade 逻辑仍在当前工作树中。
                current_revision = self.db_revision()
                if not current_revision or current_revision != self.previous_db_revision:
                    self.run(
                        [str(self.venv_python), "-m", "alembic", "downgrade", self.previous_db_revision],
                        cwd=self.root / "backend",
                        timeout=1800,
                        env_overrides=self.migration_env(),
                    )
        except Exception as exc:
            rollback_errors.append(f"数据库迁移回退失败：{exc}")

        try:
            if self.git_switched and self.previous_sha:
                self.git("reset", "--hard", self.previous_sha)
        except Exception as exc:
            rollback_errors.append(f"代码回退失败：{exc}")

        try:
            if "backend/requirements.txt" in self.changed_files:
                self.run([str(self.pip), "install", "-r", "requirements.txt"], cwd=self.root / "backend", timeout=3600)
        except Exception as exc:
            rollback_errors.append(f"后端依赖恢复失败：{exc}")

        try:
            if any(path in self.changed_files for path in ("frontend/package.json", "frontend/package-lock.json")):
                command = ["npm", "ci"] if (self.root / "frontend" / "package-lock.json").is_file() else ["npm", "install"]
                self.run(command, cwd=self.root / "frontend", timeout=3600)
            if any(path.startswith("frontend/") for path in self.changed_files):
                self.run(["npm", "run", "build"], cwd=self.root / "frontend", timeout=3600)
        except Exception as exc:
            rollback_errors.append(f"前端恢复失败：{exc}")

        try:
            self.run(["make", "restart"], timeout=120)
            if not self.check_health(attempts=30):
                rollback_errors.append("回滚版本健康检查未通过")
        except Exception as exc:
            rollback_errors.append(f"服务恢复失败：{exc}")

        if rollback_errors:
            self.status(
                "failed", 100, "自动回滚未完全成功，需要人工处理",
                error=cause,
                rollbackErrors=rollback_errors,
                finishedAt=now_iso(),
                lastInstallResult="failed",
                lastInstallAt=now_iso(),
                lastInstallFromSha=self.previous_sha,
                lastInstallToSha=self.args.target,
            )
            self.history("failed", "更新失败且自动回滚未完全成功", error=cause, rollbackErrors=rollback_errors)
            return False

        self.set_maintenance(False)
        self.status(
            "rolled_back", 100, "更新失败，已自动恢复上一版本",
            error=cause,
            rollbackErrors=[],
            currentSha=self.previous_sha,
            finishedAt=now_iso(),
            lastInstallResult="rolled_back",
            lastInstallAt=now_iso(),
            lastInstallFromSha=self.previous_sha,
            lastInstallToSha=self.args.target,
        )
        self.history("rolled_back", "更新失败，已自动恢复上一版本", error=cause)
        return True

    def execute(self) -> int:
        self.claim_update_lock()
        try:
            return self._execute_locked()
        finally:
            self.release_update_lock()

    def _execute_locked(self) -> int:
        self.log(f"开始系统更新：{self.args.branch} -> {self.args.target}")
        try:
            self.preflight()
            if self.previous_sha == self.args.target:
                self.status(
                    "success", 100, "当前已经是目标版本",
                    currentSha=self.previous_sha,
                    finishedAt=now_iso(),
                    error="",
                    rollbackCause="",
                    rollbackErrors=[],
                    lastInstallResult="success",
                    lastInstallAt=now_iso(),
                    lastInstallFromSha=self.previous_sha,
                    lastInstallToSha=self.args.target,
                )
                self.history("success", "无需更新，当前已经是目标版本")
                return 0
            self.set_maintenance(True, "系统正在安装更新，业务操作已暂时锁定")
            # API 已进入维护模式后先暂停后台写入，再备份，避免数据库与 data/ 时间点漂移。
            self.quiesce_workers()
            self.backup()
            self.switch_code()
            self.install_dependencies()
            self.migrate()
            self.build_frontend()
            self.restart()
            self.set_maintenance(False)
            self.status(
                "success", 100, "系统更新完成",
                currentSha=self.args.target,
                previousSha=self.previous_sha,
                finishedAt=now_iso(),
                error="",
                rollbackCause="",
                rollbackErrors=[],
                lastInstallResult="success",
                lastInstallAt=now_iso(),
                lastInstallFromSha=self.previous_sha,
                lastInstallToSha=self.args.target,
            )
            self.history("success", "系统更新完成")
            return 0
        except Exception as exc:
            message = str(exc)
            self.log("更新失败：" + message)
            if self.git_switched:
                self.rollback(message)
            else:
                if self.workers_quiesced:
                    try:
                        self.run(["make", "restart"], timeout=120)
                    except Exception as restart_exc:
                        message = f"{message}；恢复后台服务失败：{restart_exc}"
                self.set_maintenance(False)
                self.status(
                    "failed", 100, "更新在切换代码前失败，未修改当前版本",
                    error=message,
                    finishedAt=now_iso(),
                    lastInstallResult="failed",
                    lastInstallAt=now_iso(),
                    lastInstallFromSha=self.previous_sha,
                    lastInstallToSha=self.args.target,
                )
                self.history("failed", "更新在切换代码前失败", error=message)
            return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--remote", required=True)
    parser.add_argument("--actor", default="system")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--health-url", default="http://127.0.0.1:8000/healthz")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(Runner(parse_args()).execute())
