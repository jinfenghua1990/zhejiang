from app.config import settings
from app.models.integration import IntegrationCredential
from app.services import r2_backup_service


def test_r2_config_encrypts_secrets_and_never_exposes_them(db_session, monkeypatch):
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "pytest-r2-secret-for-fernet")

    saved = r2_backup_service.save_config(
        db_session,
        endpoint_url="https://account-id.r2.cloudflarestorage.com",
        bucket="ecommerce-backup",
        prefix="ecommerce-workspace/backup",
        access_key="R2_AK_TEST_12345678",
        secret_key="R2_SK_TEST_SUPER_SECRET",
        enabled=True,
        full_interval_days=10,
        actor="pytest",
    )

    assert saved["configured"] is True
    assert saved["enabled"] is True
    assert saved["readEnabled"] is True
    assert saved["fullIntervalDays"] == 10
    assert saved["accessKeyHint"].endswith("5678")
    assert "access_key" not in saved
    assert "secret_key" not in saved

    row = (
        db_session.query(IntegrationCredential)
        .filter_by(provider=r2_backup_service.PROVIDER)
        .order_by(IntegrationCredential.id.desc())
        .first()
    )
    assert row is not None
    assert "R2_AK_TEST_12345678" not in row.secret_encrypted
    assert "R2_SK_TEST_SUPER_SECRET" not in row.secret_encrypted

    runtime = r2_backup_service.runtime_config(db_session)
    assert runtime is not None
    assert runtime["access_key"] == "R2_AK_TEST_12345678"
    assert runtime["secret_key"] == "R2_SK_TEST_SUPER_SECRET"


def test_r2_config_update_keeps_existing_secret_when_fields_are_blank(db_session, monkeypatch):
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "pytest-r2-secret-for-fernet")

    r2_backup_service.save_config(
        db_session,
        endpoint_url="https://account-id.r2.cloudflarestorage.com",
        bucket="ecommerce-backup",
        prefix="ecommerce-workspace/backup",
        access_key="R2_AK_TEST_12345678",
        secret_key="R2_SK_TEST_SUPER_SECRET",
        enabled=True,
        full_interval_days=10,
        actor="pytest",
    )
    updated = r2_backup_service.save_config(
        db_session,
        endpoint_url="https://account-id.r2.cloudflarestorage.com",
        bucket="ecommerce-backup-v2",
        prefix="ecommerce-workspace/backup-v2",
        access_key="",
        secret_key="",
        enabled=False,
        full_interval_days=14,
        actor="pytest",
    )

    assert updated["configured"] is True
    assert updated["enabled"] is False
    assert updated["bucket"] == "ecommerce-backup-v2"
    assert updated["fullIntervalDays"] == 14

    runtime = r2_backup_service.runtime_config(db_session)
    assert runtime is not None
    assert runtime["access_key"] == "R2_AK_TEST_12345678"
    assert runtime["secret_key"] == "R2_SK_TEST_SUPER_SECRET"


def test_r2_config_api_never_returns_secret(client):
    response = client.put(
        "/api/v1/integrations/r2-backup",
        json={
            "endpointUrl": "https://account-id.r2.cloudflarestorage.com",
            "bucket": "ecommerce-backup-api",
            "prefix": "ecommerce-workspace/backup",
            "accessKey": "R2_AK_API_87654321",
            "secretKey": "R2_SK_API_SUPER_SECRET",
            "enabled": True,
            "fullIntervalDays": 10,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["readEnabled"] is True
    assert "secretKey" not in payload
    assert "accessKey" not in payload
    assert payload["accessKeyHint"].endswith("4321")

    fetched = client.get("/api/v1/integrations/r2-backup")
    assert fetched.status_code == 200
    fetched_payload = fetched.json()
    assert fetched_payload["bucket"] == "ecommerce-backup-api"
    assert fetched_payload["readEnabled"] is True
    assert "secretKey" not in fetched_payload
    assert "accessKey" not in fetched_payload


def test_r2_connection_test_uses_head_and_never_mutates_bucket(db_session, monkeypatch):
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "pytest-r2-secret-for-fernet")
    r2_backup_service.save_config(
        db_session,
        endpoint_url="https://account-id.r2.cloudflarestorage.com",
        bucket="ecommerce-backup",
        prefix="ecommerce-workspace/backup",
        access_key="R2_AK_TEST_12345678",
        secret_key="R2_SK_TEST_SUPER_SECRET",
        enabled=True,
        full_interval_days=10,
        actor="pytest",
    )

    calls = []

    class Response:
        status_code = 200

    def fake_head(url, *, headers, timeout, allow_redirects):
        calls.append(
            {
                "url": url,
                "headers": headers,
                "timeout": timeout,
                "allow_redirects": allow_redirects,
            }
        )
        return Response()

    monkeypatch.setattr(r2_backup_service.requests, "head", fake_head)
    result = r2_backup_service.test_connection(db_session)

    assert result["ok"] is True
    assert result["status"] == 200
    assert result["bucket"] == "ecommerce-backup"
    assert len(calls) == 1
    assert calls[0]["url"].endswith("/ecommerce-backup")
    assert calls[0]["allow_redirects"] is False
    assert "Authorization" in calls[0]["headers"]


def test_r2_connection_test_maps_auth_failure_without_leaking_secret(db_session, monkeypatch):
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "pytest-r2-secret-for-fernet")
    r2_backup_service.save_config(
        db_session,
        endpoint_url="https://account-id.r2.cloudflarestorage.com",
        bucket="ecommerce-backup",
        prefix="ecommerce-workspace/backup",
        access_key="R2_AK_TEST_12345678",
        secret_key="R2_SK_TEST_SUPER_SECRET",
        enabled=True,
        full_interval_days=10,
        actor="pytest",
    )

    class Response:
        status_code = 403

    monkeypatch.setattr(
        r2_backup_service.requests,
        "head",
        lambda *args, **kwargs: Response(),
    )

    try:
        r2_backup_service.test_connection(db_session)
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert "鉴权失败" in message
    assert "R2_SK_TEST_SUPER_SECRET" not in message



def test_r2_restore_prepare_rejects_snapshot_outside_configured_prefix():
    import pytest

    with pytest.raises(ValueError, match="恢复点路径无效"):
        r2_backup_service.start_restore_prepare(
            "other-prefix/full/2026/09/20/snapshot.json",
            prefix="ecommerce-workspace/backup",
        )


def test_r2_restore_prepare_passes_selected_snapshot_to_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    captured = {}

    class DummyProcess:
        pass

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return DummyProcess()

    monkeypatch.setattr(r2_backup_service.subprocess, "Popen", fake_popen)

    snapshot_key = "ecommerce-workspace/backup/full/2026/09/20/20260920_120000/snapshot.json"
    result = r2_backup_service.start_restore_prepare(
        snapshot_key,
        prefix="ecommerce-workspace/backup",
    )

    assert result["started"] is True
    assert result["snapshotObjectKey"] == snapshot_key
    assert "--snapshot-key" in captured["command"]
    assert snapshot_key in captured["command"]
