import asyncio
from dataclasses import dataclass
from typing import Any

import httpx


class DockhandConfigurationError(RuntimeError):
    pass


class DockhandDeployError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContainerState:
    identifier: str
    status: str | None
    running: bool
    health_status: str | None
    raw: dict[str, Any]

    @property
    def is_started(self) -> bool:
        if self.health_status:
            return self.running and self.health_status == "healthy"
        return self.running


@dataclass(frozen=True)
class ContainerDeployResult:
    stopped_container: str
    started_container: str
    status: str | None
    health_status: str | None


class DockhandClient:
    def __init__(
        self,
        url: str | None,
        token: str | None,
        *,
        env: int | None = None,
        verify_timeout_seconds: float = 60,
        verify_interval_seconds: float = 2,
        client: httpx.AsyncClient | None = None,
    ):
        self.url = url.rstrip("/") if url else None
        self.token = token
        self.env = env
        self.verify_timeout_seconds = verify_timeout_seconds
        self.verify_interval_seconds = verify_interval_seconds
        self.client = client or httpx.AsyncClient(timeout=60)

    def _require_configured(self) -> None:
        if not self.url:
            raise DockhandConfigurationError("TASK_RUNNER_DOCKHAND_URL is required")
        if not self.token:
            raise DockhandConfigurationError("TASK_RUNNER_DOCKHAND_TOKEN is required")
        if not self.token.startswith("dh_"):
            raise DockhandConfigurationError("TASK_RUNNER_DOCKHAND_TOKEN must be a Dockhand API token")

    def _params(self) -> dict[str, int]:
        return {"env": self.env} if self.env is not None else {}

    def _headers(self) -> dict[str, str]:
        self._require_configured()
        assert self.token is not None
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    async def stop_container(self, container: str) -> dict[str, Any]:
        return await self._post(f"/api/containers/{container}/stop")

    async def start_container(self, container: str) -> dict[str, Any]:
        return await self._post(f"/api/containers/{container}/start")

    async def get_container(self, container: str) -> ContainerState:
        response = await self.client.get(
            f"{self.url}/api/containers/{container}",
            headers=self._headers(),
            params=self._params(),
        )
        response.raise_for_status()
        data = _unwrap(response.json())
        return _container_state(container, data)

    async def deploy_container_swap(self, stop_container: str, start_container: str) -> ContainerDeployResult:
        if not stop_container or not start_container:
            raise ValueError("stop_container and start_container are required")
        await self.stop_container(stop_container)
        await self.start_container(start_container)
        state = await self.wait_until_started(start_container)
        return ContainerDeployResult(
            stopped_container=stop_container,
            started_container=start_container,
            status=state.status,
            health_status=state.health_status,
        )

    async def container_uses_volume(self, container: str, volume_name: str) -> bool:
        if not volume_name:
            raise ValueError("volume_name is required")
        state = await self.get_container(container)
        return _container_uses_volume(state.raw, volume_name)

    async def wait_until_started(self, container: str) -> ContainerState:
        deadline = asyncio.get_running_loop().time() + self.verify_timeout_seconds
        last_state: ContainerState | None = None
        while True:
            last_state = await self.get_container(container)
            if last_state.is_started:
                return last_state
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(self.verify_interval_seconds)
        health = f", health={last_state.health_status}" if last_state and last_state.health_status else ""
        status = last_state.status if last_state else "unknown"
        raise DockhandDeployError(f"Container '{container}' did not become healthy before timeout: status={status}{health}")

    async def _post(self, path: str) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.url}{path}",
            headers=self._headers(),
            json={},
            params=self._params(),
        )
        response.raise_for_status()
        return response.json()


def _unwrap(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DockhandDeployError("Dockhand returned a non-object container response")
    for key in ("container", "data", "result"):
        nested = value.get(key)
        if isinstance(nested, dict):
            return nested
    return value


def _container_state(identifier: str, data: dict[str, Any]) -> ContainerState:
    state = _case_insensitive_dict(data.get("state") or data.get("State") or {})
    health = _case_insensitive_dict(state.get("health") or state.get("Health") or {})
    status = _lower(data.get("status") or data.get("Status") or state.get("status"))
    health_status = _lower(
        data.get("health")
        or data.get("Health")
        or data.get("health_status")
        or data.get("healthStatus")
        or health.get("status")
    )
    running_value = data.get("running")
    if running_value is None:
        running_value = data.get("Running")
    if running_value is None:
        running_value = state.get("running")
    running = bool(running_value) if running_value is not None else status == "running"
    return ContainerState(identifier=identifier, status=status, running=running, health_status=health_status, raw=data)


def _container_uses_volume(data: dict[str, Any], volume_name: str) -> bool:
    mounts = data.get("mounts") or data.get("Mounts") or []
    if not isinstance(mounts, list):
        return False
    for mount in mounts:
        if not isinstance(mount, dict):
            continue
        name = mount.get("name") or mount.get("Name")
        mount_type = _lower(mount.get("type") or mount.get("Type"))
        source = str(mount.get("source") or mount.get("Source") or "")
        if name == volume_name or (mount_type == "volume" and source.endswith(f"/{volume_name}")):
            return True
    return False


def _case_insensitive_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key).lower(): item for key, item in value.items()}


def _lower(value: Any) -> str | None:
    return str(value).lower() if value is not None else None
