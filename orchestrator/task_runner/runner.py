import asyncio

import httpx


class RunnerClient:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=30)

    async def execute(self, url: str, repo: str, issue_number: int, prompt: str) -> str:
        response = await self.client.post(
            f"{url.rstrip('/')}/execute", json={"repo": repo, "issue_number": issue_number, "prompt": prompt}
        )
        response.raise_for_status()
        return response.json()["execution_id"]

    async def wait_until_ready(self, url: str, timeout: float, interval: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        last_error: Exception | None = None
        while True:
            try:
                response = await self.client.get(f"{url.rstrip('/')}/")
                response.raise_for_status()
                return
            except httpx.HTTPError as exc:
                last_error = exc
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError(f"Runner shim at {url} did not become ready") from last_error
            await asyncio.sleep(interval)

    async def status(self, url: str, execution_id: str) -> dict:
        response = await self.client.get(f"{url.rstrip('/')}/status/{execution_id}")
        response.raise_for_status()
        return response.json()

    async def resume(
        self, url: str, repo: str, issue_number: int, prompt: str, session_id: str
    ) -> str:
        response = await self.client.post(
            f"{url.rstrip('/')}/resume",
            json={
                "repo": repo,
                "issue_number": issue_number,
                "prompt": prompt,
                "session_id": session_id,
            },
        )
        response.raise_for_status()
        return response.json()["execution_id"]

    async def result(self, url: str, execution_id: str) -> dict:
        response = await self.client.get(f"{url.rstrip('/')}/result/{execution_id}")
        response.raise_for_status()
        return response.json()

    async def cancel(self, url: str, execution_id: str) -> bool:
        try:
            response = await self.client.delete(f"{url.rstrip('/')}/execute/{execution_id}")
            return response.is_success
        except httpx.HTTPError:
            return False

    async def codex_version(self, url: str) -> dict:
        response = await self.client.get(f"{url.rstrip('/')}/codex/version")
        response.raise_for_status()
        return response.json()
