from app.config import settings
from app.models.integration import IntegrationCredential
from app.services import kodo_backup_service


def test_kodo_config_encrypts_secrets_and_never_exposes_them(db_session, monkeypatch):
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "pytest-kodo-secret-for-fernet")

    saved = kodo_backup_service.save_config(
        db_session,
        bucket="cold-backup-test",
        upload_url="https://up-test.example.invalid",
        prefix="ecommerce-workspace/cold",
        access_key="AK_TEST_12345678",
        secret_key="SK_TEST_SUPER_SECRET",
        enabled=True,
        actor="pytest",
    )

    assert saved["configured"] is True
    assert saved["enabled"] is True
    assert saved["readEnabled"] is False
    assert saved["mode"] == "upload_only"
    assert saved["accessKeyHint"].endswith("5678")
    assert "access_key" not in saved
    assert "secret_key" not in saved

    row = (
        db_session.query(IntegrationCredential)
        .filter_by(provider=kodo_backup_service.PROVIDER)
        .order_by(IntegrationCredential.id.desc())
        .first()
    )
    assert row is not None
    assert "AK_TEST_12345678" not in row.secret_encrypted
    assert "SK_TEST_SUPER_SECRET" not in row.secret_encrypted

    runtime = kodo_backup_service.runtime_config(db_session)
    assert runtime is not None
    assert runtime["access_key"] == "AK_TEST_12345678"
    assert runtime["secret_key"] == "SK_TEST_SUPER_SECRET"
    assert runtime["readEnabled"] is False


def test_kodo_config_update_keeps_existing_secret_when_fields_are_blank(db_session, monkeypatch):
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "pytest-kodo-secret-for-fernet")

    kodo_backup_service.save_config(
        db_session,
        bucket="cold-backup-test",
        upload_url="https://up-test.example.invalid",
        prefix="ecommerce-workspace/cold",
        access_key="AK_TEST_12345678",
        secret_key="SK_TEST_SUPER_SECRET",
        enabled=True,
        actor="pytest",
    )
    updated = kodo_backup_service.save_config(
        db_session,
        bucket="cold-backup-test-2",
        upload_url="https://up-test-2.example.invalid",
        prefix="ecommerce-workspace/cold-v2",
        access_key="",
        secret_key="",
        enabled=False,
        actor="pytest",
    )

    assert updated["configured"] is True
    assert updated["enabled"] is False
    assert updated["bucket"] == "cold-backup-test-2"

    runtime = kodo_backup_service.runtime_config(db_session)
    assert runtime is not None
    assert runtime["access_key"] == "AK_TEST_12345678"
    assert runtime["secret_key"] == "SK_TEST_SUPER_SECRET"


def test_kodo_config_api_never_returns_secret(client):
    response = client.put(
        "/api/v1/integrations/kodo-cold",
        json={
            "bucket": "cold-backup-api-test",
            "uploadUrl": "https://up-api-test.example.invalid",
            "prefix": "ecommerce-workspace/cold",
            "accessKey": "AK_API_TEST_87654321",
            "secretKey": "SK_API_TEST_SUPER_SECRET",
            "enabled": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["readEnabled"] is False
    assert "secretKey" not in payload
    assert "accessKey" not in payload
    assert payload["accessKeyHint"].endswith("4321")

    fetched = client.get("/api/v1/integrations/kodo-cold")
    assert fetched.status_code == 200
    fetched_payload = fetched.json()
    assert fetched_payload["bucket"] == "cold-backup-api-test"
    assert fetched_payload["readEnabled"] is False
    assert "secretKey" not in fetched_payload
    assert "accessKey" not in fetched_payload



def _load_kodo_upload_module():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "kodo-cold-upload.py"
    spec = spec_from_file_location("kodo_cold_upload_test_module", script)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kodo_content_object_key_is_stable_and_content_addressed():
    module = _load_kodo_upload_module()
    digest = "ab" * 32

    key1 = module.content_object_key("ecommerce-workspace/cold", digest)
    key2 = module.content_object_key("ecommerce-workspace/cold", digest)

    assert key1 == key2
    assert key1 == f"ecommerce-workspace/cold/objects/sha256/ab/{digest}"


def test_kodo_local_receipts_rebuild_dedup_index_without_remote_read(tmp_path):
    import json

    module = _load_kodo_upload_module()
    receipt_dir = tmp_path / ".kodo-uploaded"
    receipt_dir.mkdir()

    digest_old = "11" * 32
    digest_new = "22" * 32
    (receipt_dir / "legacy.json").write_text(
        json.dumps({
            "files": [{
                "sha256": digest_old,
                "object_key": f"cold/objects/sha256/11/{digest_old}",
            }]
        }),
        encoding="utf-8",
    )
    (receipt_dir / "new.json").write_text(
        json.dumps({
            "objects": [{
                "sha256": digest_new,
                "objectKey": f"cold/objects/sha256/22/{digest_new}",
            }]
        }),
        encoding="utf-8",
    )

    known = module.load_known_objects(receipt_dir)

    assert known[digest_old].endswith(digest_old)
    assert known[digest_new].endswith(digest_new)


def test_kodo_insert_only_conflict_is_safe_retry_without_remote_read(tmp_path, monkeypatch):
    module = _load_kodo_upload_module()
    source = tmp_path / "payload.bin"
    source.write_bytes(b"same-content")

    class FakeResponse:
        status_code = 614
        text = "file exists"

        def json(self):
            return {"error": "file exists"}

    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse()

    monkeypatch.setattr(module.requests, "post", fake_post)

    result = module.upload_one(
        upload_url="https://upload.example.invalid",
        access_key="AK_TEST",
        secret_key="SK_TEST",
        bucket="cold",
        object_key=module.content_object_key("cold", module.sha256_file(source)),
        source=source,
    )

    assert result["already_exists"] is True
    assert result["status"] == 614
    assert len(calls) == 1


def test_kodo_upload_script_has_no_remote_read_requests():
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "kodo-cold-upload.py"
    source = script.read_text(encoding="utf-8")

    assert "requests.get(" not in source
    assert "requests.head(" not in source
    assert "requests.delete(" not in source
    assert "requests.put(" not in source
    assert "requests.post(" in source

def test_kodo_write_probe_only_posts_and_never_reads(db_session, monkeypatch):
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "pytest-kodo-write-probe-secret")
    kodo_backup_service.save_config(
        db_session,
        bucket="cold-write-probe",
        upload_url="https://upload.example.invalid",
        prefix="ecommerce-workspace/cold",
        access_key="AK_WRITE_PROBE",
        secret_key="SK_WRITE_PROBE",
        enabled=True,
        actor="pytest",
    )

    calls = []

    class Response:
        status_code = 200
        text = '{"key":"ok"}'

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(kodo_backup_service.requests, "post", fake_post)
    result = kodo_backup_service.test_write_connection(db_session)

    assert result["ok"] is True
    assert "只写测试通过" in result["message"]
    assert len(calls) == 1
    assert calls[0][0] == "https://upload.example.invalid"
    assert calls[0][1]["data"]["key"].endswith("/_healthcheck/write-probe-v1.txt")


def test_kodo_start_backup_runs_detached_executor(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    captured = {}

    class DummyProcess:
        pass

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return DummyProcess()

    monkeypatch.setattr(kodo_backup_service.subprocess, "Popen", fake_popen)
    result = kodo_backup_service.start_backup()

    assert result["started"] is True
    assert result["target"] == "kodo"
    assert result["mode"] == "full"
    assert captured["command"][-1].endswith("scripts/kodo-cold-upload.py")
    assert captured["kwargs"]["start_new_session"] is True
