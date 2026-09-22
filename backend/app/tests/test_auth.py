"""登录鉴权测试：口令散列 / 令牌签验 / 登录流程 / 未授权 401 / RBAC。"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.auth import create_token, decode_token, hash_password, verify_password
from app.config import settings
from app.main import app
from app.models.org import AuditLog, Role, User, UserRole


def test_password_hash_roundtrip():
    hashed = hash_password("s3cret-密码")
    assert hashed != "s3cret-密码"
    assert verify_password("s3cret-密码", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("s3cret-密码", None)


def test_token_roundtrip():
    token = create_token(uid=42, username="alice")
    payload = decode_token(token)
    assert payload is not None
    assert payload["sub"] == "alice"
    assert payload["uid"] == 42
    assert payload["ver"] == 0


def test_token_tampered_rejected():
    token = create_token(uid=42, username="alice")
    assert decode_token(token[:-2] + "xx") is None
    assert decode_token("") is None
    assert decode_token("not-a-token") is None


@pytest.mark.skipif(settings.ACCESS_MODE == "open", reason="open 模式按局域网访问，不启用接口鉴权")
def test_protected_endpoint_requires_token(db_session):
    """无令牌访问受保护接口必须 401。"""
    with TestClient(app) as anon:
        r = anon.get("/api/v1/system/health")
        assert r.status_code == 401
        r = anon.get("/api/v1/purchase/orders")
        assert r.status_code == 401


@pytest.mark.skipif(settings.ACCESS_MODE == "open", reason="open 模式按局域网访问，不启用接口鉴权")
def test_query_string_token_is_rejected(client):
    user = client._pytest_user
    token = create_token(uid=user.id, username=user.username, token_version=user.token_version)
    r = client.get(
        f"/api/v1/system/health?access_token={token}",
        headers={"Authorization": ""},
    )
    assert r.status_code == 401


def test_auth_config_public(db_session):
    """前端必须能在无 token 状态读取真实 access mode。"""
    with TestClient(app) as anon:
        r = anon.get("/api/v1/auth/config")
        assert r.status_code == 200
        assert r.json()["accessMode"] == settings.ACCESS_MODE


def test_healthz_public(db_session):
    """健康检查保持公开（容器探针依赖）。"""
    with TestClient(app) as anon:
        r = anon.get("/healthz")
        assert r.status_code == 200


@pytest.mark.skipif(settings.ACCESS_MODE == "open", reason="open 模式不依赖登录态确定当前操作者")
def test_login_success_and_me(client, db_session):
    user = User(
        username="alice",
        display_name="爱丽丝",
        hashed_password=hash_password("alice-pass-123"),
    )
    db_session.add(user)
    db_session.flush()
    role = db_session.scalar(select(Role).where(Role.code == "admin"))
    db_session.add(UserRole(user_id=user.id, role_id=role.id))
    db_session.flush()

    r = client.post("/api/v1/auth/login", json={"username": "alice", "password": "alice-pass-123"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accessToken"]
    assert body["user"]["username"] == "alice"
    assert "admin" in body["user"]["roles"]

    # 新令牌可用
    r2 = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['accessToken']}"}
    )
    assert r2.status_code == 200
    assert r2.json()["username"] == "alice"

    # 清理（login 的 audit 已 commit，需显式删除避免脏数据）
    db_session.execute(delete(AuditLog).where(AuditLog.actor == "alice"))
    db_session.execute(delete(UserRole).where(UserRole.user_id == user.id))
    db_session.execute(delete(User).where(User.id == user.id))
    db_session.commit()


@pytest.mark.skipif(settings.ACCESS_MODE == "open", reason="open 模式不依赖登录态确定当前操作者")
def test_login_rehashes_legacy_pbkdf2_work_factor(client, db_session):
    """旧 120k PBKDF2 hash 仍可登录，并在成功登录后平滑升级为当前 600k。"""
    password = "legacy-pass-123"
    salt = b"legacy-salt-1234"
    iterations = 120_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    legacy_hash = f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"
    user = User(username="legacy-user", display_name="", hashed_password=legacy_hash)
    db_session.add(user)
    db_session.flush()
    user_id = user.id

    r = client.post(
        "/api/v1/auth/login",
        json={"username": "legacy-user", "password": password},
    )
    assert r.status_code == 200, r.text

    db_session.expire_all()
    fresh = db_session.get(User, user_id)
    assert fresh is not None
    assert fresh.hashed_password != legacy_hash
    assert fresh.hashed_password.startswith("pbkdf2_sha256$600000$")
    assert verify_password(password, fresh.hashed_password)

    db_session.execute(delete(AuditLog).where(AuditLog.actor == "legacy-user"))
    db_session.execute(delete(User).where(User.id == user_id))
    db_session.commit()


def test_login_wrong_password(client, db_session):
    user = User(username="bob", display_name="", hashed_password=hash_password("bob-pass-123"))
    db_session.add(user)
    db_session.flush()

    r = client.post("/api/v1/auth/login", json={"username": "bob", "password": "wrong"})
    assert r.status_code == 401

    db_session.execute(delete(AuditLog).where(AuditLog.actor == "bob"))
    db_session.execute(delete(User).where(User.id == user.id))
    db_session.commit()


@pytest.mark.skipif(settings.ACCESS_MODE == "open", reason="open 模式不启用账号密码链路")
def test_change_password(client, db_session):
    user = User(username="carol", display_name="", hashed_password=hash_password("old-pass-123"))
    db_session.add(user)
    db_session.flush()
    token = create_token(uid=user.id, username="carol")
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "wrong-old", "new_password": "new-pass-456"},
        headers=headers,
    )
    assert r.status_code == 400

    r = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "old-pass-123", "new_password": "new-pass-456"},
        headers=headers,
    )
    assert r.status_code == 200

    db_session.expire_all()
    fresh = db_session.get(User, user.id)
    assert verify_password("new-pass-456", fresh.hashed_password)
    assert fresh.token_version == 1

    # 改密后此前签发的令牌立即失效，新密码可重新登录。
    expired = client.get("/api/v1/auth/me", headers=headers)
    assert expired.status_code == 401
    relogin = client.post(
        "/api/v1/auth/login",
        json={"username": "carol", "password": "new-pass-456", "remember_me": True},
    )
    assert relogin.status_code == 200
    payload = decode_token(relogin.json()["accessToken"])
    assert payload is not None
    assert payload["ver"] == 1

    db_session.execute(delete(AuditLog).where(AuditLog.actor == "carol"))
    db_session.execute(delete(User).where(User.id == user.id))
    db_session.commit()


@pytest.mark.skipif(settings.ACCESS_MODE == "open", reason="open 模式不启用账号密码链路")
def test_logout_revokes_existing_token(client, db_session):
    user = User(username="dave", display_name="", hashed_password=hash_password("dave-pass-123"))
    db_session.add(user)
    db_session.flush()
    token = create_token(uid=user.id, username=user.username, token_version=user.token_version)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401

    db_session.execute(delete(AuditLog).where(AuditLog.actor == "dave"))
    db_session.execute(delete(User).where(User.id == user.id))
    db_session.commit()


@pytest.mark.skipif(settings.ACCESS_MODE == "open", reason="open 模式不启用 RBAC 读写限制")
def test_viewer_is_read_only(client, db_session):
    viewer_role = db_session.scalar(select(Role).where(Role.code == "viewer"))
    user = User(username="view-only", display_name="", hashed_password=hash_password("view-pass-123"))
    db_session.add(user)
    db_session.flush()
    db_session.add(UserRole(user_id=user.id, role_id=viewer_role.id))
    db_session.flush()
    token = create_token(uid=user.id, username=user.username, token_version=user.token_version)
    headers = {"Authorization": f"Bearer {token}"}
    db_session.expire_all()

    assert client.get("/api/v1/reconciliation/rules", headers=headers).status_code == 200
    denied = client.post(
        "/api/v1/reconciliation/rules",
        headers=headers,
        json={"match_pattern": "test", "platform": "test"},
    )
    assert denied.status_code == 403

    db_session.execute(delete(UserRole).where(UserRole.user_id == user.id))
    db_session.execute(delete(User).where(User.id == user.id))
    db_session.commit()
