from pathlib import Path

from task_runner.ops_images import ImageTag, codex_runner_tag, prune_candidates


def test_codex_runner_tag_includes_version_and_repo_sha_to_prevent_dockerfile_collisions():
    assert codex_runner_tag("0.144.1", "a1b2c3d") == "0.144.1-a1b2c3d"
    assert codex_runner_tag("0.144.1", "d4e5f6a") != codex_runner_tag("0.144.1", "a1b2c3d")


def test_prune_candidates_keep_current_plus_n_minus_one():
    tags = [
        ImageTag("0.142.5-old0001", "2026-07-09T00:00:00Z"),
        ImageTag("0.144.0-mid0002", "2026-07-10T00:00:00Z"),
        ImageTag("0.144.1-new0003", "2026-07-11T00:00:00Z"),
    ]

    assert prune_candidates(tags, keep=2) == ["0.142.5-old0001"]


def test_ops_workflow_does_not_touch_running_codex_runner_container():
    workflow = Path(".github/workflows/ops-codex-runner-rebuild.yml").read_text()

    assert "docker stop" not in workflow
    assert "docker rm" not in workflow
    assert "docker run" not in workflow
    assert "stop, start" not in workflow
