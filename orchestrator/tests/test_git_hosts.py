import base64
from typing import Any

import httpx
import pytest

from task_runner.forgejo import ForgejoClient
from task_runner.git_host import GitHostClient, GitHostError, Milestone
from task_runner.github import GitHubClient


def client_for(handler, base_url: str = "https://host.test") -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)


def assert_contract(client: GitHostClient) -> None:
    assert isinstance(client, GitHostClient)


@pytest.mark.parametrize("client_type", [GitHubClient, ForgejoClient])
def test_clients_conform_to_protocol(client_type) -> None:
    client = (
        client_type("https://forgejo.test", client=client_for(lambda _: None))
        if client_type is ForgejoClient
        else client_type(client=client_for(lambda _: None))
    )
    assert_contract(client)


@pytest.mark.asyncio
async def test_github_contract_and_normalization() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/issues/7"):
            return httpx.Response(200, json={"title": "Issue", "body": None})
        if "/contents/" in path:
            return httpx.Response(
                200,
                json={
                    "type": "file",
                    "encoding": "base64",
                    "content": base64.b64encode(b"rules").decode(),
                },
            )
        if path.endswith("/dispatches"):
            return httpx.Response(204)
        if path.endswith("/pulls"):
            return httpx.Response(
                201,
                json={
                    "number": 8,
                    "html_url": "https://github.test/pr/8",
                    "extra": True,
                },
            )
        if path.endswith("/comments"):
            return httpx.Response(
                201,
                json={
                    "id": 9,
                    "html_url": "https://github.test/comment/9",
                    "body": "ok",
                },
            )
        if path.endswith("/milestones") and request.method == "GET":
            return httpx.Response(200, json=[github_milestone()])
        if path.endswith("/milestones"):
            return httpx.Response(201, json=github_milestone())
        if "/milestones/3" in path and request.method == "DELETE":
            return httpx.Response(204)
        if "/milestones/3" in path:
            return httpx.Response(200, json=github_milestone())
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = GitHubClient(client=client_for(handler, "https://api.github.test"))
    assert (await client.get_issue_context("owner/repo", 7)).body == ""
    assert await client.get_text_file("owner/repo", "docs/rules.md", "dev") == "rules"
    await client.dispatch_workflow(
        "owner/repo", "test workflow.yml", "dev", {"key": "value"}
    )
    assert (
        await client.create_pull_request("owner/repo", "title", "body", "head", "dev")
    ).number == 8
    assert (await client.post_comment("owner/repo", 7, "ok")).id == "9"
    assert await client.list_milestones("owner/repo") == [expected_milestone()]
    assert (
        await client.create_milestone(
            "owner/repo", "Phase", "Desc", "2026-09-01T12:00:00Z"
        )
        == expected_milestone()
    )
    assert await client.get_milestone("owner/repo", "3") == expected_milestone()
    assert (
        await client.update_milestone("owner/repo", "3", state="closed")
        == expected_milestone()
    )
    await client.delete_milestone("owner/repo", "3")

    assert requests[1].url.params["ref"] == "dev"
    assert requests[2].url.raw_path.endswith(b"/test%20workflow.yml/dispatches")
    assert requests[2].read() == b'{"ref":"dev","inputs":{"key":"value"}}'


@pytest.mark.asyncio
async def test_forgejo_contract_uses_confirmed_shapes_and_verbs() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/issues/7"):
            return httpx.Response(
                200, json={"number": 7, "title": "Issue", "body": "Body"}
            )
        if "/contents/" in path:
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": base64.b64encode(b"rules").decode(),
                },
            )
        if path.endswith("/dispatches"):
            return httpx.Response(201, json={"id": 99})
        if path.endswith("/pulls"):
            return httpx.Response(
                201,
                json={
                    "number": 1,
                    "html_url": "https://forgejo.test/pr/1",
                    "base": {"ref": "dev"},
                    "head": {"ref": "head"},
                },
            )
        if path.endswith("/comments"):
            return httpx.Response(
                201,
                json={
                    "id": 2,
                    "html_url": "https://forgejo.test/comment/2",
                    "body": "ok",
                },
            )
        if path.endswith("/milestones") and request.method == "GET":
            return httpx.Response(200, json=[forgejo_milestone()])
        if path.endswith("/milestones"):
            return httpx.Response(201, json=forgejo_milestone())
        if "/milestones/1" in path and request.method == "DELETE":
            return httpx.Response(204)
        if "/milestones/1" in path:
            return httpx.Response(200, json=forgejo_milestone())
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = ForgejoClient(
        "https://forgejo.test/", "secret", client=client_for(handler)
    )
    assert (await client.get_issue_context("owner/repo", 7)).title == "Issue"
    assert await client.get_text_file("owner/repo", "AGENTS.md") == "rules"
    await client.dispatch_workflow("owner/repo", "build.yml", "dev", {})
    assert (
        await client.create_pull_request("owner/repo", "title", "body", "head", "dev")
    ).number == 1
    assert (await client.post_comment("owner/repo", 7, "ok")).id == "2"
    assert await client.list_milestones("owner/repo", "all") == [
        expected_milestone(id="1")
    ]
    assert await client.create_milestone(
        "owner/repo", "Phase", "Desc"
    ) == expected_milestone(id="1")
    assert await client.get_milestone("owner/repo", "1") == expected_milestone(id="1")
    assert await client.update_milestone(
        "owner/repo", "1", state="closed"
    ) == expected_milestone(id="1")
    await client.delete_milestone("owner/repo", "1")

    assert requests[0].url.path.startswith("/repos/")
    assert requests[2].read() == b'{"ref":"dev","inputs":{}}'
    patch = next(request for request in requests if request.method == "PATCH")
    assert patch.url.path.endswith("/milestones/1")
    assert patch.read() == b'{"state":"closed"}'


@pytest.mark.asyncio
async def test_forgejo_default_transport_uses_api_root_and_token_auth() -> None:
    client = ForgejoClient("https://forgejo.test/root/", "pat")
    assert str(client.client.base_url) == "https://forgejo.test/root/api/v1/"
    assert client.client.headers["Authorization"] == "token pat"
    await client.client.aclose()


@pytest.mark.asyncio
async def test_missing_file_is_normalized_to_none() -> None:
    response = lambda _: httpx.Response(
        404, json={"message": "The target couldn't be found.", "errors": []}
    )
    assert (
        await ForgejoClient(
            "https://forgejo.test", client=client_for(response)
        ).get_text_file("o/r", "missing")
        is None
    )
    assert (
        await GitHubClient(client=client_for(response)).get_text_file("o/r", "missing")
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body"),
    [
        (
            401,
            {"message": "token is required", "url": "https://forgejo.test/api/swagger"},
        ),
        (
            403,
            {
                "message": "token does not have at least one of required scope(s): [write:admin]",
                "url": "https://forgejo.test/api/swagger",
            },
        ),
        (
            404,
            {
                "message": "The target couldn't be found.",
                "url": "https://forgejo.test/api/swagger",
                "errors": [],
            },
        ),
        (
            422,
            {
                "message": '[]: parsing time "bad" as "2006-01-02T15:04:05Z07:00"',
                "url": "https://forgejo.test/api/swagger",
            },
        ),
    ],
)
async def test_forgejo_translates_observed_errors(
    status: int, body: dict[str, Any]
) -> None:
    client = ForgejoClient(
        "https://forgejo.test",
        client=client_for(lambda _: httpx.Response(status, json=body)),
    )
    with pytest.raises(GitHostError) as caught:
        await client.get_milestone("owner/repo", "1")
    assert caught.value.status_code == status
    assert caught.value.message == body["message"]
    assert caught.value.provider == "Forgejo"


@pytest.mark.asyncio
async def test_forgejo_rejects_deadline_clearing_without_request() -> None:
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=forgejo_milestone())

    client = ForgejoClient("https://forgejo.test", client=client_for(handler))
    with pytest.raises(ValueError, match="does not support clearing"):
        await client.update_milestone("owner/repo", "1", due_on=None)
    assert not called


def github_milestone() -> dict[str, Any]:
    return {
        "number": 3,
        "title": "Phase",
        "description": "Desc",
        "state": "open",
        "due_on": "2026-09-01T12:00:00Z",
        "html_url": "https://github.test/milestones/3",
    }


def forgejo_milestone() -> dict[str, Any]:
    return {
        "id": 1,
        "title": "Phase",
        "description": "Desc",
        "state": "open",
        "open_issues": 0,
        "closed_issues": 0,
        "created_at": "2026-08-12T01:25:12Z",
        "updated_at": "2026-08-12T01:25:12Z",
        "closed_at": None,
        "due_on": "2026-09-01T12:00:00Z",
    }


def expected_milestone(id: str = "3") -> Milestone:
    return Milestone(
        id=id,
        title="Phase",
        description="Desc",
        state="open",
        due_on="2026-09-01T12:00:00Z",
    )
