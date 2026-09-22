from __future__ import annotations

import py_compile
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "script",
    [
        "scripts/backup.sh",
        "scripts/full-backup.sh",
        "scripts/backup-schedule.sh",
        "scripts/native-start.sh",
        "scripts/restore-check.sh",
    ],
)
def test_backup_shell_scripts_parse(script):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not installed")
    subprocess.run([bash, "-n", str(ROOT / script)], check=True)


@pytest.mark.parametrize(
    "script",
    [
        "scripts/backup-config-crypto.py",
        "scripts/kodo-cold-upload.py",
        "scripts/webdav-backup.py",
        "scripts/r2-backup.py",
        "scripts/r2-restore.py",
    ],
)
def test_backup_python_scripts_compile(script, tmp_path):
    py_compile.compile(str(ROOT / script), cfile=str(tmp_path / (Path(script).name + ".pyc")), doraise=True)



def _load_backup_crypto_module():
    import importlib.util

    path = ROOT / "scripts" / "backup-config-crypto.py"
    spec = importlib.util.spec_from_file_location("backup_config_crypto_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backup_config_crypto_round_trip_uses_external_key(tmp_path):
    module = _load_backup_crypto_module()
    source = tmp_path / "config.tar.gz"
    encrypted = tmp_path / "config.tar.gz.fernet"
    restored = tmp_path / "restored.tar.gz"
    key_file = tmp_path / "external" / "backup-recovery.key"
    source.write_bytes(b"runtime-config-secret-payload")

    fingerprint = module.encrypt_file(source, encrypted, key_file, create_key=True)
    assert encrypted.is_file()
    assert encrypted.read_bytes() != source.read_bytes()
    assert key_file.is_file()
    assert fingerprint == module.key_fingerprint(key_file.read_bytes().strip())

    restored_fingerprint = module.decrypt_file(encrypted, restored, key_file)
    assert restored_fingerprint == fingerprint
    assert restored.read_bytes() == source.read_bytes()



def test_kodo_cold_backup_stays_remote_write_only():
    script = (ROOT / "scripts" / "kodo-cold-upload.py").read_text(encoding="utf-8")
    assert "requests.post(" in script
    for forbidden in ("requests.get(", "requests.head(", "requests.put(", "requests.delete("):
        assert forbidden not in script

    routes = (ROOT / "backend" / "app" / "api" / "v1" / "integrations.py").read_text(encoding="utf-8")
    assert '@router.post("/kodo-cold/test"' not in routes
    assert '@router.post("/kodo-cold/restore"' not in routes
    assert '@router.get("/kodo-cold/objects"' not in routes
