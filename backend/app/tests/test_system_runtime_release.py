from app.api.v1 import system as system_api


class _ScalarResult:
    def __init__(self, value: str):
        self.value = value

    def scalar(self):
        return self.value


class _FakeDb:
    def execute(self, _statement):
        return _ScalarResult("abc123schema")


def test_runtime_release_info_for_container(monkeypatch):
    monkeypatch.setattr(system_api.settings, "APP_ENV", "staging")
    monkeypatch.setattr(system_api.settings, "RELEASE_CHANNEL", "candidate")
    monkeypatch.setattr(system_api.settings, "DEPLOYMENT_MODE", "container")
    monkeypatch.setattr(system_api.settings, "GIT_SHA", "1234567890abcdef")
    monkeypatch.setattr(
        system_api.settings,
        "APP_IMAGE_REF",
        "ghcr.io/jinfenghua1990/zhejiang:sha-1234567890abcdef",
    )
    monkeypatch.setattr(system_api.settings, "SYSTEM_UPDATE_ENABLED", False)

    result = system_api._runtime_release_info(_FakeDb())

    assert result["appEnv"] == "staging"
    assert result["releaseChannel"] == "candidate"
    assert result["deploymentMode"] == "container"
    assert result["managedBy"] == "github_ghcr"
    assert result["gitSha"] == "1234567890abcdef"
    assert result["imageTag"] == "sha-1234567890abcdef"
    assert result["version"] == "sha-1234567890abcdef"
    assert result["alembicRevision"] == "abc123schema"
    assert result["inAppUpdateEnabled"] is False


def test_container_readiness_reports_runtime_identity(monkeypatch):
    monkeypatch.setattr(system_api.settings, "DEPLOYMENT_MODE", "container")
    monkeypatch.setattr(system_api.settings, "GIT_SHA", "abcdef1234567890")
    monkeypatch.setattr(
        system_api.settings,
        "APP_IMAGE_REF",
        "ghcr.io/jinfenghua1990/zhejiang:candidate",
    )

    result = system_api.system_update_readiness(_FakeDb())

    assert result["ready"] is True
    assert result["blockingCount"] == 0
    keys = {item["key"] for item in result["checks"]}
    assert keys == {"container_image", "git_sha", "database_revision"}
