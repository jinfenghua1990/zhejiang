from app.services import backup_schedule_service


def test_non_macos_scheduler_is_not_reported_as_managed(monkeypatch):
    monkeypatch.setattr(backup_schedule_service.platform, "system", lambda: "Linux")

    status = backup_schedule_service.get_schedule_status("r2")

    assert status["mode"] == "external"
    assert status["managed"] is False
    assert "NAS" in status["message"]
