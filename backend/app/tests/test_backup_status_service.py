from __future__ import annotations

import json

from app.services import backup_status_service


def test_r2_full_receipt_exposes_recoverable_snapshot_key(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    receipt_dir = tmp_path / ".r2-uploaded"
    receipt_dir.mkdir()
    snapshot_key = (
        "ecommerce-workspace/backup/full/2026/09/20/"
        "20260920_120000/snapshot.json"
    )
    (receipt_dir / "20260920_120000-full.json").write_text(
        json.dumps(
            {
                "timestamp": "20260920_120000",
                "kind": "full",
                "snapshotObjectKey": snapshot_key,
            }
        ),
        encoding="utf-8",
    )

    status = backup_status_service.get_status()

    record = status["lastR2Full"]
    assert record is not None
    assert record["type"] == "r2_full"
    assert record["recoverable"] is True
    assert record["snapshotObjectKey"] == snapshot_key


def test_r2_daily_receipt_is_not_marked_recoverable(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    receipt_dir = tmp_path / ".r2-uploaded"
    receipt_dir.mkdir()
    (receipt_dir / "20260920_120000-daily.json").write_text(
        json.dumps(
            {
                "timestamp": "20260920_120000",
                "kind": "daily",
                "snapshotObjectKey": (
                    "ecommerce-workspace/backup/daily/2026/09/20/"
                    "20260920_120000.json"
                ),
            }
        ),
        encoding="utf-8",
    )

    status = backup_status_service.get_status()

    record = status["lastR2"]
    assert record is not None
    assert record["type"] == "r2_daily"
    assert record["recoverable"] is False

def test_backup_status_exposes_failed_latest_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    status_dir = tmp_path / ".job-status"
    status_dir.mkdir()
    (status_dir / "r2.json").write_text(
        json.dumps(
            {
                "target": "r2",
                "status": "failed",
                "startedAt": "2099-09-21T03:00:00+08:00",
                "finishedAt": "2099-09-21T03:00:03+08:00",
                "exitCode": 2,
                "mode": "backup",
            }
        ),
        encoding="utf-8",
    )

    status = backup_status_service.get_status()

    assert status["lastAttemptR2"]["status"] == "failed"
    assert status["health"]["r2"]["status"] == "failed"
    assert status["health"]["kodo"]["status"] == "never"


def test_kodo_receipt_reports_upload_acknowledged_not_online_recoverable(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    receipt_dir = tmp_path / ".kodo-uploaded"
    receipt_dir.mkdir()
    (receipt_dir / "20260921_040000.json").write_text(
        json.dumps(
            {
                "timestamp": "20260921_040000",
                "snapshotObjectKey": "ecommerce-workspace/cold/snapshots/2026/09/21/20260921_040000/snapshot.json",
                "uploadedCount": 3,
                "reusedCount": 4,
            }
        ),
        encoding="utf-8",
    )

    status = backup_status_service.get_status()

    record = status["lastKodo"]
    assert record["verificationLevel"] == "upload_acknowledged"
    assert record["recoverable"] is False
    assert record["uploadedCount"] == 3
    assert record["reusedCount"] == 4
