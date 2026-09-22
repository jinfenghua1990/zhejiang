from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def _load_drift_migration():
    backend_root = Path(__file__).resolve().parents[2]
    path = backend_root / "alembic" / "versions" / "drift20260920_align_supplier_nullable.py"
    spec = spec_from_file_location("drift20260920_test_module", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_finance_fk_helper_tolerates_existing_identical_constraint(db_session):
    connection = db_session.connection()
    connection.execute(text("DROP TABLE IF EXISTS pytest_fk_source"))
    connection.execute(text("DROP TABLE IF EXISTS pytest_fk_target"))
    connection.execute(text("CREATE TEMP TABLE pytest_fk_target (id BIGINT PRIMARY KEY) ON COMMIT DROP"))
    connection.execute(
        text(
            "CREATE TEMP TABLE pytest_fk_source ("
            "id BIGINT PRIMARY KEY, "
            "target_id BIGINT NOT NULL"
            ") ON COMMIT DROP"
        )
    )

    operations = Operations(MigrationContext.configure(connection))
    migration = _load_drift_migration()

    migration._ensure_fk(
        "fk_pytest_duplicate",
        "pytest_fk_source",
        "pytest_fk_target",
        ["target_id"],
        ["id"],
        ondelete="CASCADE",
        operations=operations,
    )
    # This second call reproduces the production state that used to raise
    # psycopg.errors.DuplicateObject.
    migration._ensure_fk(
        "fk_pytest_duplicate",
        "pytest_fk_source",
        "pytest_fk_target",
        ["target_id"],
        ["id"],
        ondelete="CASCADE",
        operations=operations,
    )

    count = connection.execute(
        text(
            """
            SELECT count(*)
            FROM pg_constraint
            WHERE conrelid = to_regclass('pytest_fk_source')
              AND conname = 'fk_pytest_duplicate'
              AND contype = 'f'
            """
        )
    ).scalar_one()
    assert count == 1
