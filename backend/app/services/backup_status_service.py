from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _backup_dir() -> Path:
    root = Path(os.getenv("PERSIST_ROOT") or str(_root())).expanduser()
    return Path(os.getenv("BACKUP_DIR") or (root / "backups")).expanduser()


def _timezone() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("TZ") or "Asia/Shanghai")
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def _iso_from_timestamp(value: str) -> str | None:
    try:
        parsed = datetime.strptime(value[:15], "%Y%m%d_%H%M%S").replace(tzinfo=_timezone())
        return parsed.isoformat()
    except Exception:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _local_records(directory: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for manifest in directory.glob("backup_*.manifest"):
        timestamp = manifest.stem.removeprefix("backup_")
        records.append(
            {
                "timestamp": timestamp,
                "time": _iso_from_timestamp(timestamp),
                "type": "local",
                "target": "本地",
                "status": "success",
                "detail": "PostgreSQL + data/ 基础恢复点",
                "recoverable": True,
                "verificationLevel": "local_manifest",
            }
        )
    return records


def _r2_records(directory: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    receipt_dir = directory / ".r2-uploaded"
    if not receipt_dir.is_dir():
        return records
    for receipt in receipt_dir.glob("*.json"):
        payload = _read_json(receipt)
        timestamp = str(payload.get("timestamp") or receipt.stem.split("-", 1)[0])
        kind = str(payload.get("kind") or "daily")
        full = kind == "full"
        snapshot_key = str(payload.get("snapshotObjectKey") or "")
        records.append(
            {
                "timestamp": timestamp,
                "time": _iso_from_timestamp(timestamp),
                "type": "r2_full" if full else "r2_daily",
                "target": "Cloudflare R2",
                "status": "success",
                "detail": "完整容灾 · 可由系统准备恢复" if full else "模块化快照 · 上传成功",
                "snapshotObjectKey": snapshot_key,
                "recoverable": full and bool(snapshot_key),
                "verificationLevel": "restore_ready" if full and snapshot_key else "uploaded",
            }
        )
    return records


def _kodo_records(directory: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    receipt_dir = directory / ".kodo-uploaded"
    if not receipt_dir.is_dir():
        return records
    for receipt in receipt_dir.glob("*.json"):
        payload = _read_json(receipt)
        timestamp = str(payload.get("timestamp") or receipt.stem)
        records.append(
            {
                "timestamp": timestamp,
                "time": _iso_from_timestamp(timestamp),
                "type": "kodo_full",
                "target": "七牛云 Kodo",
                "status": "success",
                "detail": "完整冷备 · 上传响应已确认 · 系统不读取远端",
                "snapshotObjectKey": str(payload.get("snapshotObjectKey") or ""),
                "recoverable": False,
                "verificationLevel": "upload_acknowledged",
                "uploadedCount": int(payload.get("uploadedCount") or 0),
                "reusedCount": int(payload.get("reusedCount") or 0),
            }
        )
    return records


def _webdav_records(directory: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    receipt_dir = directory / ".webdav-uploaded"
    if not receipt_dir.is_dir():
        return records
    for receipt in receipt_dir.glob("*.json"):
        payload = _read_json(receipt)
        timestamp = str(payload.get("timestamp") or receipt.stem)
        records.append(
            {
                "timestamp": timestamp,
                "time": _iso_from_timestamp(timestamp),
                "type": "webdav_full",
                "target": "坚果云 WebDAV",
                "status": "success",
                "detail": "完整容灾 · WebDAV 上传回执已确认 · 系统恢复执行器待接入",
                "snapshotObjectKey": str(payload.get("snapshotObjectKey") or ""),
                "recoverable": False,
                "verificationLevel": "uploaded",
                "uploadedCount": len(payload.get("objects") or []),
            }
        )
    return records


def _job_attempt(directory: Path, target: str) -> dict[str, Any] | None:
    payload = _read_json(directory / ".job-status" / f"{target}.json")
    if not payload:
        return None
    return {
        "target": target,
        "status": str(payload.get("status") or ""),
        "startedAt": payload.get("startedAt"),
        "finishedAt": payload.get("finishedAt"),
        "exitCode": payload.get("exitCode"),
        "mode": str(payload.get("mode") or ""),
    }


def _attempt_record(attempt: dict[str, Any] | None, *, target_label: str, type_name: str) -> dict[str, Any] | None:
    if not attempt or attempt.get("status") == "success":
        return None
    started = _parse_iso(attempt.get("startedAt"))
    timestamp = started.astimezone(_timezone()).strftime("%Y%m%d_%H%M%S") if started else ""
    state = str(attempt.get("status") or "")
    detail_map = {
        "running": "任务正在执行",
        "failed": "最近一次任务执行失败",
        "skipped": "任务已触发但未产生新的恢复点（可能被锁或配置停用）",
    }
    return {
        "timestamp": timestamp,
        "time": attempt.get("startedAt"),
        "type": type_name,
        "target": target_label,
        "status": state,
        "detail": detail_map.get(state, "任务状态记录"),
        "recoverable": False,
        "verificationLevel": "job_attempt",
    }


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=_timezone())
    except Exception:
        return None


def _health(last_success: dict[str, Any] | None, attempt: dict[str, Any] | None, *, stale_hours: int) -> dict[str, Any]:
    now = datetime.now(_timezone())
    last_time = _parse_iso((last_success or {}).get("time"))
    attempt_time = _parse_iso((attempt or {}).get("startedAt"))
    attempt_status = str((attempt or {}).get("status") or "")

    if attempt_status == "running":
        return {"status": "running", "ageHours": None, "message": "任务正在执行"}
    if attempt_status == "failed" and (last_time is None or (attempt_time and attempt_time > last_time)):
        return {"status": "failed", "ageHours": None, "message": "最近一次任务失败"}

    if last_time is None:
        return {"status": "never", "ageHours": None, "message": "尚无成功恢复点"}

    age_hours = max(0.0, (now - last_time.astimezone(_timezone())).total_seconds() / 3600)
    if age_hours > stale_hours:
        return {
            "status": "stale",
            "ageHours": round(age_hours, 1),
            "message": f"最近成功已超过 {stale_hours} 小时",
        }
    return {"status": "healthy", "ageHours": round(age_hours, 1), "message": "最近成功时间正常"}


def get_status(limit: int = 50) -> dict[str, Any]:
    directory = _backup_dir()
    if not directory.is_dir():
        empty = {"status": "never", "ageHours": None, "message": "尚无成功恢复点"}
        return {
            "backupDir": str(directory),
            "records": [],
            "lastLocal": None,
            "lastR2": None,
            "lastR2Full": None,
            "lastKodo": None,
            "lastWebdav": None,
            "lastAttemptR2": None,
            "lastAttemptKodo": None,
            "lastAttemptWebdav": None,
            "health": {"local": empty, "r2": empty, "kodo": empty, "webdav": empty},
        }

    success_records = _local_records(directory) + _r2_records(directory) + _kodo_records(directory) + _webdav_records(directory)
    success_records.sort(key=lambda row: str(row.get("timestamp") or ""), reverse=True)

    def first(types: set[str]) -> dict[str, Any] | None:
        return next((row for row in success_records if row["type"] in types), None)

    last_local = first({"local"})
    last_r2 = first({"r2_daily", "r2_full"})
    last_r2_full = first({"r2_full"})
    last_kodo = first({"kodo_full"})
    last_webdav = first({"webdav_full"})
    last_attempt_r2 = _job_attempt(directory, "r2")
    last_attempt_kodo = _job_attempt(directory, "kodo")
    last_attempt_webdav = _job_attempt(directory, "webdav")

    records = list(success_records)
    for attempt_record in (
        _attempt_record(last_attempt_r2, target_label="Cloudflare R2", type_name="r2_attempt"),
        _attempt_record(last_attempt_kodo, target_label="七牛云 Kodo", type_name="kodo_attempt"),
        _attempt_record(last_attempt_webdav, target_label="坚果云 WebDAV", type_name="webdav_attempt"),
    ):
        if attempt_record:
            records.append(attempt_record)
    records.sort(key=lambda row: str(row.get("timestamp") or ""), reverse=True)

    return {
        "backupDir": str(directory),
        "records": records[: max(1, min(limit, 200))],
        "lastLocal": last_local,
        "lastR2": last_r2,
        "lastR2Full": last_r2_full,
        "lastKodo": last_kodo,
        "lastWebdav": last_webdav,
        "lastAttemptR2": last_attempt_r2,
        "lastAttemptKodo": last_attempt_kodo,
        "lastAttemptWebdav": last_attempt_webdav,
        "health": {
            "local": _health(last_local, None, stale_hours=36),
            "r2": _health(last_r2, last_attempt_r2, stale_hours=36),
            "kodo": _health(last_kodo, last_attempt_kodo, stale_hours=36),
            "webdav": _health(last_webdav, last_attempt_webdav, stale_hours=36),
        },
    }
