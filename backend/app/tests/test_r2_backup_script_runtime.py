from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _load_r2_backup_module():
    path = ROOT / "scripts" / "r2-backup.py"
    spec = importlib.util.spec_from_file_location("r2_backup_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r2_daily_deduplicates_base_manifest_and_modules(tmp_path, monkeypatch):
    module = _load_r2_backup_module()
    timestamp = "20260920_120000"

    files = {}
    for name, payload in {
        f"backup_{timestamp}.manifest": b"base-manifest",
        f"db_{timestamp}.dump": b"db",
        f"data_{timestamp}.tar.gz": b"data",
        f"app_{timestamp}.tar.gz": b"app",
        f"config_{timestamp}.tar.gz.fernet": b"config",
        f"full_{timestamp}.manifest": b"full-manifest",
    }.items():
        path = tmp_path / name
        path.write_bytes(payload)
        files[name] = path

    def digest(path: Path) -> str:
        return module.sha256_file(path)

    values = {
        "timestamp": timestamp,
        "git_sha": "deadbeef",
        "base_manifest": f"backup_{timestamp}.manifest",
        "base_manifest_sha256": digest(files[f"backup_{timestamp}.manifest"]),
        "db": f"db_{timestamp}.dump",
        "db_sha256": digest(files[f"db_{timestamp}.dump"]),
        "data": f"data_{timestamp}.tar.gz",
        "data_sha256": digest(files[f"data_{timestamp}.tar.gz"]),
        "app": f"app_{timestamp}.tar.gz",
        "app_sha256": digest(files[f"app_{timestamp}.tar.gz"]),
        "config": f"config_{timestamp}.tar.gz.fernet",
        "config_sha256": digest(files[f"config_{timestamp}.tar.gz.fernet"]),
        "config_encryption": "fernet",
        "config_key_fingerprint": "0123456789abcdef",
        "config_key_external_required": "1",
        "docker_image": "",
        "docker_image_sha256": "",
    }
    monkeypatch.setattr(
        module,
        "prepare_full_snapshot",
        lambda *, include_docker: (files[f"full_{timestamp}.manifest"], values),
    )

    uploaded_files: list[tuple[str, str]] = []
    uploaded_indexes: list[str] = []
    monkeypatch.setattr(
        module,
        "put_file",
        lambda config, object_key, source: uploaded_files.append((object_key, source.name)),
    )
    monkeypatch.setattr(
        module,
        "put_bytes",
        lambda config, object_key, payload, content_type: uploaded_indexes.append(object_key),
    )

    state: dict = {}
    snapshot = module.run_daily({"prefix": "ecommerce-workspace/backup"}, tmp_path, state)

    base_state = state["modules"]["base_manifest"]
    assert base_state["object_key"].startswith(
        "ecommerce-workspace/backup/modules/base_manifest/"
    )
    assert snapshot["modules"]["base_manifest"]["objectKey"] == base_state["object_key"]
    assert snapshot["configEncryption"] == "fernet"
    assert snapshot["configKeyFingerprint"] == "0123456789abcdef"
    assert snapshot["configKeyExternalRequired"] is True
    assert len(uploaded_files) == 5
    assert any(key.endswith("/latest-daily.json") for key in uploaded_indexes)

    uploaded_files.clear()
    uploaded_indexes.clear()
    module.run_daily({"prefix": "ecommerce-workspace/backup"}, tmp_path, state)

    assert uploaded_files == []
    assert len(uploaded_indexes) == 2
