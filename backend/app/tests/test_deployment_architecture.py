from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def _text(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def test_container_images_startup_have_no_implicit_database_migration():
    for relative in ("Dockerfile", "backend/Dockerfile"):
        dockerfile = _text(relative)
        cmd_lines = [line.strip() for line in dockerfile.splitlines() if line.strip().startswith("CMD ")]
        assert cmd_lines, f"{relative} must define a runtime CMD"
        assert all("alembic" not in line.lower() for line in cmd_lines)
        assert any("uvicorn" in line.lower() for line in cmd_lines)


def test_root_compose_has_explicit_migration_lifecycle():
    compose = _text("docker-compose.yml")
    assert "\n  migrate:\n" in compose
    assert "MIGRATION_DATABASE_URL" in compose
    assert 'MIGRATION_DATABASE_URL: ""' in compose
    assert 'POSTGRES_PASSWORD: ""' in compose
    assert "condition: service_completed_successfully" in compose


def test_zspace_compose_separates_runtime_and_migration_database_roles():
    compose = _text("deploy/zspace/docker-compose.yml")
    assert "\n  db-roles:\n" in compose
    assert "\n  migrate:\n" in compose
    assert "APP_DB_USER" in compose
    assert "MIGRATOR_DB_USER" in compose
    assert "MIGRATION_DATABASE_URL" in compose
    assert "ALTER SCHEMA public OWNER TO" in compose
    assert 'MIGRATION_DATABASE_URL: ""' in compose
    assert 'MIGRATOR_DB_PASSWORD: ""' in compose
    assert 'POSTGRES_PASSWORD: ""' in compose
    assert 'APP_DB_PASSWORD: ""' in compose
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES" in compose


def test_production_env_documents_distinct_app_and_migrator_connections():
    env = _text("deploy/zspace/production.env.example")
    assert "APP_DB_USER=ecommerce_app" in env
    assert "MIGRATOR_DB_USER=ecommerce_migrator" in env
    assert "DATABASE_URL=postgresql+psycopg://ecommerce_app:" in env
    assert "MIGRATION_DATABASE_URL=postgresql+psycopg://ecommerce_migrator:" in env
