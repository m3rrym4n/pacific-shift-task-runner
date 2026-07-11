from pathlib import Path
import importlib.util
import urllib.request

from task_runner.ops_images import ImageTag, codex_runner_tag, prune_candidates


def load_prune_script():
    path = Path("scripts/prune_zot_image_tags.py")
    spec = importlib.util.spec_from_file_location("prune_zot_image_tags", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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


def test_ops_workflow_uses_unauthenticated_zot_registry():
    workflow = Path(".github/workflows/ops-codex-runner-rebuild.yml").read_text()

    assert "docker/login-action" not in workflow
    assert "ZOT_USERNAME" not in workflow
    assert "ZOT_PASSWORD" not in workflow
    assert "ZOT_TOKEN" not in workflow
    assert "--token-env" not in workflow


def test_prune_script_missing_token_env_uses_no_authorization_header(monkeypatch):
    prune = load_prune_script()
    captured = {}

    class FakeResponse:
        status = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"tags":[]}'

    def fake_urlopen(req, timeout, context):
        captured["authorization"] = req.get_header("Authorization")
        return FakeResponse()

    monkeypatch.delenv("ZOT_TOKEN", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    token = prune.optional_token_from_env("ZOT_TOKEN")
    status, _, _ = prune.request("https://registry.example/v2/codex-runner/tags/list", token=token)

    assert status == 200
    assert token is None
    assert captured["authorization"] is None
