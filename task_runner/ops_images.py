from dataclasses import dataclass


def codex_runner_tag(codex_version: str, repo_short_sha: str) -> str:
    if not codex_version or not repo_short_sha:
        raise ValueError("codex_version and repo_short_sha are required")
    return f"{codex_version}-{repo_short_sha}"


@dataclass(frozen=True)
class ImageTag:
    name: str
    created: str


def prune_candidates(tags: list[ImageTag], keep: int = 2) -> list[str]:
    if keep < 1:
        raise ValueError("keep must be at least one")
    ordered = sorted(tags, key=lambda tag: tag.created, reverse=True)
    return [tag.name for tag in ordered[keep:]]
