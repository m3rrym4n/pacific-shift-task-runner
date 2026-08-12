import base64
from typing import Any
from urllib.parse import quote

import httpx

from .git_host import CommentRef, GitHostError, IssueContext, Milestone, PullRequestRef


class ForgejoClient:
    def __init__(
        self,
        host_base_url: str,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"token {token}"
        self.client = client or httpx.AsyncClient(
            base_url=f"{host_base_url.rstrip('/')}/api/v1", headers=headers, timeout=30
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await self.client.request(method, path, **kwargs)
        if response.is_error:
            try:
                payload = response.json()
                message = (
                    payload.get("message", response.text)
                    if isinstance(payload, dict)
                    else response.text
                )
            except ValueError:
                message = response.text or response.reason_phrase
            raise GitHostError("Forgejo", response.status_code, str(message))
        return response

    async def get_issue_context(self, repo: str, number: int) -> IssueContext:
        value = (await self._request("GET", f"/repos/{repo}/issues/{number}")).json()
        return IssueContext(title=value["title"], body=value.get("body") or "")

    async def get_text_file(
        self, repo: str, path: str, ref: str | None = None
    ) -> str | None:
        response = await self.client.get(
            f"/repos/{repo}/contents/{quote(path, safe='/')}",
            params={"ref": ref} if ref else None,
        )
        if response.status_code == 404:
            return None
        if response.is_error:
            await self._request_from_response(response)
        return base64.b64decode(response.json()["content"]).decode("utf-8")

    async def _request_from_response(self, response: httpx.Response) -> None:
        try:
            payload = response.json()
            message = (
                payload.get("message", response.text)
                if isinstance(payload, dict)
                else response.text
            )
        except ValueError:
            message = response.text or response.reason_phrase
        raise GitHostError("Forgejo", response.status_code, str(message))

    async def dispatch_workflow(
        self, repo: str, workflow: str, ref: str, inputs: dict[str, Any]
    ) -> None:
        await self._request(
            "POST",
            f"/repos/{repo}/actions/workflows/{quote(workflow, safe='')}/dispatches",
            json={"ref": ref, "inputs": inputs},
        )

    async def create_pull_request(
        self, repo: str, title: str, body: str, head: str, base: str
    ) -> PullRequestRef:
        value = (
            await self._request(
                "POST",
                f"/repos/{repo}/pulls",
                json={"title": title, "body": body, "head": head, "base": base},
            )
        ).json()
        return PullRequestRef(number=value["number"], url=value["html_url"])

    async def post_comment(self, repo: str, number: int, body: str) -> CommentRef:
        value = (
            await self._request(
                "POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body}
            )
        ).json()
        return CommentRef(id=str(value["id"]), url=value["html_url"])

    @staticmethod
    def _milestone(value: dict[str, Any]) -> Milestone:
        return Milestone(
            id=str(value["id"]),
            title=value["title"],
            description=value.get("description"),
            state=value["state"],
            due_on=value.get("due_on"),
        )

    async def list_milestones(self, repo: str, state: str = "open") -> list[Milestone]:
        milestones: list[Milestone] = []
        page = 1
        while True:
            values = (
                await self._request(
                    "GET",
                    f"/repos/{repo}/milestones",
                    params={"state": state, "limit": 50, "page": page},
                )
            ).json()
            milestones.extend(self._milestone(value) for value in values)
            if len(values) < 50:
                return milestones
            page += 1

    async def create_milestone(
        self,
        repo: str,
        title: str,
        description: str | None = None,
        due_on: str | None = None,
    ) -> Milestone:
        payload = {"title": title, "description": description}
        if due_on is not None:
            payload["due_on"] = due_on
        value = (
            await self._request("POST", f"/repos/{repo}/milestones", json=payload)
        ).json()
        return self._milestone(value)

    async def get_milestone(self, repo: str, id: str) -> Milestone:
        value = (
            await self._request("GET", f"/repos/{repo}/milestones/{quote(id, safe='')}")
        ).json()
        return self._milestone(value)

    async def update_milestone(self, repo: str, id: str, **fields: Any) -> Milestone:
        """Update supported fields; Forgejo 16 cannot clear an existing deadline.

        The server returns 200 for ``due_on: null`` while retaining the old value,
        so rejecting that operation prevents callers from observing false success.
        """
        if "due_on" in fields and fields["due_on"] is None:
            raise ValueError("Forgejo does not support clearing a milestone deadline")
        value = (
            await self._request(
                "PATCH", f"/repos/{repo}/milestones/{quote(id, safe='')}", json=fields
            )
        ).json()
        return self._milestone(value)

    async def delete_milestone(self, repo: str, id: str) -> None:
        await self._request("DELETE", f"/repos/{repo}/milestones/{quote(id, safe='')}")
