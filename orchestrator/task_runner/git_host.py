from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class IssueContext:
    title: str
    body: str


@dataclass(frozen=True)
class PullRequestRef:
    number: int
    url: str


@dataclass(frozen=True)
class CommentRef:
    id: str
    url: str


@dataclass(frozen=True)
class Milestone:
    id: str
    title: str
    description: str | None
    state: str
    due_on: str | None


class GitHostError(RuntimeError):
    def __init__(self, provider: str, status_code: int, message: str):
        self.provider = provider
        self.status_code = status_code
        self.message = message
        super().__init__(f"{provider} API error ({status_code}): {message}")


@runtime_checkable
class GitHostClient(Protocol):
    async def get_issue_context(self, repo: str, number: int) -> IssueContext: ...

    async def get_text_file(
        self, repo: str, path: str, ref: str | None = None
    ) -> str | None: ...

    async def dispatch_workflow(
        self, repo: str, workflow: str, ref: str, inputs: dict[str, Any]
    ) -> None: ...

    async def create_pull_request(
        self, repo: str, title: str, body: str, head: str, base: str
    ) -> PullRequestRef: ...

    async def post_comment(self, repo: str, number: int, body: str) -> CommentRef: ...

    async def list_milestones(
        self, repo: str, state: str = "open"
    ) -> list[Milestone]: ...

    async def create_milestone(
        self,
        repo: str,
        title: str,
        description: str | None = None,
        due_on: str | None = None,
    ) -> Milestone: ...

    async def get_milestone(self, repo: str, id: str) -> Milestone: ...

    async def update_milestone(
        self, repo: str, id: str, **fields: Any
    ) -> Milestone: ...

    async def delete_milestone(self, repo: str, id: str) -> None: ...
