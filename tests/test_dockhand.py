import pytest

from task_runner.dockhand import (
    ContainerSnapshot,
    DockhandClient,
    DockhandConfigurationError,
    DockhandDeployError,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeHttpClient:
    def __init__(self, states):
        self.states = iter(states)
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return FakeResponse({"success": True})

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse({"success": True})

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return FakeResponse(next(self.states))


@pytest.mark.asyncio
async def test_deploy_container_swap_stops_starts_and_verifies_health():
    http = FakeHttpClient(
        [
            {"State": {"Status": "running", "Running": True, "Health": {"Status": "starting"}}},
            {"State": {"Status": "running", "Running": True, "Health": {"Status": "healthy"}}},
        ]
    )
    client = DockhandClient(
        "http://dockhand:3003",
        "dh_test",
        env=2,
        verify_timeout_seconds=1,
        verify_interval_seconds=0,
        client=http,
    )

    result = await client.deploy_container_swap("old-container", "new-container")

    assert result.stopped_container == "old-container"
    assert result.started_container == "new-container"
    assert result.status == "running"
    assert result.health_status == "healthy"
    assert [call[0] for call in http.calls] == ["POST", "POST", "GET", "GET"]
    assert http.calls[0][1] == "http://dockhand:3003/api/containers/old-container/stop"
    assert http.calls[1][1] == "http://dockhand:3003/api/containers/new-container/start"
    assert http.calls[0][2]["headers"]["Authorization"] == "Bearer dh_test"
    assert http.calls[0][2]["params"] == {"env": 2}


@pytest.mark.asyncio
async def test_deploy_container_swap_accepts_running_container_without_healthcheck():
    http = FakeHttpClient([{"status": "running", "running": True}])
    client = DockhandClient(
        "http://dockhand:3003",
        "dh_test",
        verify_timeout_seconds=1,
        verify_interval_seconds=0,
        client=http,
    )

    result = await client.deploy_container_swap("old-container", "new-container")

    assert result.status == "running"
    assert result.health_status is None


@pytest.mark.asyncio
async def test_container_uses_volume_detects_named_volume_mount():
    http = FakeHttpClient(
        [
            {
                "status": "running",
                "running": True,
                "Mounts": [
                    {
                        "Type": "volume",
                        "Name": "pacific-shift-codex-runner-auth",
                        "Source": "/var/lib/docker/volumes/pacific-shift-codex-runner-auth/_data",
                    }
                ],
            }
        ]
    )
    client = DockhandClient("http://dockhand:3003", "dh_test", client=http)

    assert await client.container_uses_volume("codex-runner", "pacific-shift-codex-runner-auth") is True


@pytest.mark.asyncio
async def test_container_uses_volume_returns_false_when_missing():
    http = FakeHttpClient([{"status": "running", "running": True, "Mounts": []}])
    client = DockhandClient("http://dockhand:3003", "dh_test", client=http)

    assert await client.container_uses_volume("codex-runner", "pacific-shift-codex-runner-auth") is False


@pytest.mark.asyncio
async def test_deploy_container_swap_requires_dedicated_dockhand_token():
    client = DockhandClient("http://dockhand:3003", "not-a-dockhand-token", client=FakeHttpClient([]))

    with pytest.raises(DockhandConfigurationError, match="Dockhand API token"):
        await client.deploy_container_swap("old-container", "new-container")


@pytest.mark.asyncio
async def test_deploy_container_swap_fails_when_container_never_becomes_healthy():
    http = FakeHttpClient([{"status": "running", "running": True, "health": "starting"}] * 10)
    client = DockhandClient(
        "http://dockhand:3003",
        "dh_test",
        verify_timeout_seconds=0,
        verify_interval_seconds=0,
        client=http,
    )

    with pytest.raises(DockhandDeployError, match="did not become healthy"):
        await client.deploy_container_swap("old-container", "new-container")


@pytest.mark.asyncio
async def test_replace_and_restore_use_snapshot_and_verify_actual_state():
    old = {
        "Config": {
            "Image": "registry/codex:old", "Cmd": ["serve"], "Env": ["KEEP=yes"],
            "Labels": {"owner": "test"},
        },
        "HostConfig": {
            "Binds": ["auth:/home/codex/.codex:rw"],
            "PortBindings": {"7000/tcp": [{"HostPort": "7000"}]},
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
            "NetworkMode": "bridge",
        },
        "State": {"Status": "running", "Running": True},
    }
    http = FakeHttpClient(
        [
            {**old, "Config": {**old["Config"], "Image": "registry/codex:new"}},
            old,
        ]
    )
    client = DockhandClient("http://dockhand:3003", "dh_test", client=http)
    snapshot = ContainerSnapshot("codex-runner", "registry/codex:old", old)

    deployed = await client.replace_from_snapshot(snapshot, "registry/codex:new")
    restored = await client.restore_snapshot(snapshot)

    assert deployed.status == "running"
    assert restored.running is True
    creates = [call for call in http.calls if call[0] == "POST" and call[1].endswith("/api/containers")]
    assert creates[0][2]["json"]["image"] == "registry/codex:new"
    assert creates[0][2]["json"]["volumeBinds"] == ["auth:/home/codex/.codex:rw"]
    assert creates[0][2]["json"]["ports"]["7000/tcp"] == {"HostPort": "7000"}
    assert creates[1][2]["json"]["image"] == "registry/codex:old"


@pytest.mark.asyncio
async def test_replace_rejects_false_positive_when_get_reports_wrong_image():
    raw = {
        "Config": {"Image": "registry/codex:old"},
        "HostConfig": {},
        "State": {"Status": "running", "Running": True},
    }
    client = DockhandClient(
        "http://dockhand:3003", "dh_test", client=FakeHttpClient([raw])
    )

    with pytest.raises(DockhandDeployError, match="expected 'registry/codex:new'"):
        await client.replace_from_snapshot(
            ContainerSnapshot("codex-runner", "registry/codex:old", raw),
            "registry/codex:new",
        )
