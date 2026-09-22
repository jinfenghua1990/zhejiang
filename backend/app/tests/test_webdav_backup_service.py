from app.config import settings
from app.models.integration import IntegrationCredential
from app.services import webdav_backup_service


def test_webdav_config_encrypts_app_password_and_never_exposes_it(db_session, monkeypatch):
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "pytest-webdav-secret-for-fernet")

    saved = webdav_backup_service.save_config(
        db_session,
        base_url="https://dav.jianguoyun.com/dav/",
        remote_path="ecommerce-workspace/webdav",
        username="backup@example.com",
        app_password="APP_PASSWORD_SUPER_SECRET",
        enabled=True,
        actor="pytest",
    )

    assert saved["configured"] is True
    assert saved["enabled"] is True
    assert saved["readEnabled"] is True
    assert saved["baseUrl"] == "https://dav.jianguoyun.com/dav/"
    assert saved["remotePath"] == "ecommerce-workspace/webdav"
    assert "password" not in saved
    assert saved["username"] == "backup@example.com"

    row = (
        db_session.query(IntegrationCredential)
        .filter_by(provider=webdav_backup_service.PROVIDER)
        .order_by(IntegrationCredential.id.desc())
        .first()
    )
    assert row is not None
    assert "APP_PASSWORD_SUPER_SECRET" not in row.secret_encrypted

    runtime = webdav_backup_service.runtime_config(db_session)
    assert runtime is not None
    assert runtime["username"] == "backup@example.com"
    assert runtime["password"] == "APP_PASSWORD_SUPER_SECRET"


def test_webdav_update_keeps_existing_app_password_when_blank(db_session, monkeypatch):
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "pytest-webdav-update-secret")
    webdav_backup_service.save_config(
        db_session,
        base_url="https://dav.example.invalid/dav/",
        remote_path="first",
        username="first@example.com",
        app_password="APP_PASSWORD_KEEP",
        enabled=True,
        actor="pytest",
    )

    updated = webdav_backup_service.save_config(
        db_session,
        base_url="https://dav.example.invalid/dav/",
        remote_path="second",
        username="second@example.com",
        app_password="",
        enabled=False,
        actor="pytest",
    )

    assert updated["enabled"] is False
    assert updated["remotePath"] == "second"
    runtime = webdav_backup_service.runtime_config(db_session)
    assert runtime is not None
    assert runtime["username"] == "second@example.com"
    assert runtime["password"] == "APP_PASSWORD_KEEP"


def test_webdav_connection_test_reads_writes_and_cleans_probe(db_session, monkeypatch):
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "pytest-webdav-probe-secret")
    webdav_backup_service.save_config(
        db_session,
        base_url="https://dav.example.invalid/dav/",
        remote_path="ecommerce-workspace/webdav",
        username="backup@example.com",
        app_password="APP_PASSWORD_PROBE",
        enabled=True,
        actor="pytest",
    )

    calls = []

    class Response:
        def __init__(self, status_code, content=b""):
            self.status_code = status_code
            self.content = content
            self.text = ""

    class Session:
        def __init__(self):
            self.auth = None
            self.headers = {}

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return Response(207 if method == "PROPFIND" else 201)

        def put(self, url, **kwargs):
            calls.append(("PUT", url, kwargs))
            return Response(201)

        def get(self, url, **kwargs):
            calls.append(("GET", url, kwargs))
            return Response(200, b"ecommerce-workspace webdav write probe\n")

        def delete(self, url, **kwargs):
            calls.append(("DELETE", url, kwargs))
            return Response(204)

    monkeypatch.setattr(webdav_backup_service.requests, "Session", Session)
    result = webdav_backup_service.test_connection(db_session)

    assert result["ok"] is True
    assert any(method == "PROPFIND" for method, _, _ in calls)
    assert sum(method == "MKCOL" for method, _, _ in calls) == 3
    assert any(method == "PUT" for method, _, _ in calls)
    assert any(method == "GET" for method, _, _ in calls)
    assert any(method == "DELETE" for method, _, _ in calls)


def test_webdav_config_api_never_returns_app_password(client):
    response = client.put(
        "/api/v1/integrations/webdav-backup",
        json={
            "baseUrl": "https://dav.example.invalid/dav/",
            "remotePath": "ecommerce-workspace/webdav",
            "username": "backup@example.com",
            "appPassword": "APP_PASSWORD_API_SECRET",
            "enabled": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert "appPassword" not in payload
    assert "password" not in payload

    fetched = client.get("/api/v1/integrations/webdav-backup")
    assert fetched.status_code == 200
    assert fetched.json()["remotePath"] == "ecommerce-workspace/webdav"
    assert "appPassword" not in fetched.json()


def test_webdav_start_backup_runs_detached_executor(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "pytest-webdav-start-secret")
    webdav_backup_service.save_config(
        db_session,
        base_url="https://dav.example.invalid/dav/",
        remote_path="ecommerce-workspace/webdav",
        username="backup@example.com",
        app_password="APP_PASSWORD_START",
        enabled=True,
        actor="pytest",
    )
    captured = {}

    class DummyProcess:
        pass

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return DummyProcess()

    monkeypatch.setattr(webdav_backup_service.subprocess, "Popen", fake_popen)
    result = webdav_backup_service.start_backup(db_session)
    assert result["started"] is True
    assert result["target"] == "webdav"
    assert result["mode"] == "full"
    assert captured["command"][-1].endswith("scripts/webdav-backup.py")
    assert captured["kwargs"]["start_new_session"] is True
