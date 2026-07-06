import base64

import httpx


class GitHubClient:
    def __init__(self, token: str | None = None, client: httpx.AsyncClient | None = None):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = client or httpx.AsyncClient(base_url="https://api.github.com", headers=headers, timeout=30)

    async def get_context(self, repo: str, issue_number: int) -> tuple[str, str, str]:
        issue_response = await self.client.get(f"/repos/{repo}/issues/{issue_number}")
        issue_response.raise_for_status()
        issue = issue_response.json()
        agents_response = await self.client.get(f"/repos/{repo}/contents/AGENTS.md")
        if agents_response.status_code == 404:
            agents = "(No AGENTS.md found in the target repository.)"
        else:
            agents_response.raise_for_status()
            agents = base64.b64decode(agents_response.json()["content"]).decode("utf-8")
        return agents, issue["title"], issue.get("body") or ""

