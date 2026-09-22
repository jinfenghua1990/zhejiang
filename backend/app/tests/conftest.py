"""共享 pytest fixtures。

TestClient + FastAPI dependency override 模式：用真实 PG（schema 已迁移），
通过 connection-bound session + transaction rollback 实现“无副作用测试”。

不应在测试中写入会被外部接口观察到的脏数据：
- rollback 关 connection，外部 PG 连接看不到；
- 多并发测试各自绑定独立 connection，互不可见；
- TestClient 内部走 ASGI，无网络层。
"""
from collections.abc import Generator

import os

# ---- 测试库隔离（必须位于任何 app.* import 之前）----
# 聚合类测试（财务汇总/月结/税务做账）断言的是全库结果，直连业务库会被真实数据干扰。
# 设置 TEST_DATABASE_URL 后，pytest 默认连独立测试库；由于所有测试都在事务内 rollback，
# 该库会永久保持「刚迁移完」的干净状态，与 CI 行为一致。
# 临时直连业务库：PYTEST_USE_TEST_DB=0 pytest ...
from dotenv import load_dotenv

load_dotenv()  # 先把 backend/.env 载入 os.environ，否则下面的 getenv 读不到
if os.getenv("PYTEST_USE_TEST_DB", "1") == "1" and os.getenv("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from app.core.auth import create_token, hash_password
from app.db import engine, get_db
from app.main import app
from app.models.org import AuditLog, Role, User, UserRole


@pytest.fixture(scope="function")
def db_session() -> Generator:
    """每个测试独占一个 connection + transaction，session 绑该 connection。

    测试结束自动 rollback，DB 状态回到测试起点。
    """
    connection = engine.connect()
    transaction = connection.begin()
    SessionTesting = sessionmaker(
        bind=connection, autoflush=False, expire_on_commit=False
    )
    session = SessionTesting()
    try:
        yield session
    finally:
        session.close()
        try:
            transaction.rollback()
        except Exception:
            # transaction 可能因业务异常已被回滚或已 commit，吞掉避免污染 teardown
            pass
        connection.close()


@pytest.fixture(scope="function")
def client(db_session) -> Generator[TestClient, None, None]:
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db

    # 与正式 seed.py 对齐：测试环境也必须具备全部内置角色，不能依赖某次外部 seed 恰好跑过。
    builtin_roles = {
        "admin": "管理员",
        "operator": "操作员",
        "viewer": "查看者",
    }
    roles: dict[str, Role] = {}
    for code, name in builtin_roles.items():
        role = db_session.scalar(select(Role).where(Role.code == code))
        if role is None:
            role = Role(code=code, name=name)
            db_session.add(role)
            db_session.flush()
        roles[code] = role

    user = User(
        username="pytest-admin",
        display_name="测试管理员",
        hashed_password=hash_password("pytest-pass-123"),
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserRole(user_id=user.id, role_id=roles["admin"].id))
    db_session.flush()
    token = create_token(uid=user.id, username=user.username)

    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as test_client:
        test_client._pytest_user = user  # 供个别测试引用
        yield test_client
    app.dependency_overrides.clear()

    # 业务代码 commit 会绕过事务回滚，这里显式清理测试用户及其痕迹。
    # 内置角色本身与正式 seed 一致，允许保留。
    try:
        db_session.execute(delete(UserRole).where(UserRole.user_id == user.id))
        db_session.execute(delete(AuditLog).where(AuditLog.actor == user.username))
        db_session.execute(delete(User).where(User.id == user.id))
        db_session.commit()
    except Exception:
        pass
