import base64

import pytest

from codex_runner.configure_git import git_environment


def test_github_git_environment_uses_github_token():
    result = git_environment({"TASK_RUNNER_GIT_HOST": "github", "GITHUB_TOKEN": "secret"})

    assert result["GIT_CONFIG_COUNT"] == "1"
    assert result["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert result["GIT_CONFIG_VALUE_0"] == _basic_header("secret")


def test_forgejo_git_environment_uses_host_base_url_and_forgejo_token():
    result = git_environment(
        {
            "TASK_RUNNER_GIT_HOST": "forgejo",
            "TASK_RUNNER_GIT_HOST_BASE_URL": "https://forgejo.example.com/git/",
            "FORGEJO_TOKEN": "forgejo-secret",
            "GITHUB_TOKEN": "wrong-secret",
        }
    )

    assert result["GIT_CONFIG_KEY_0"] == "http.https://forgejo.example.com/git/.extraheader"
    assert result["GIT_CONFIG_VALUE_0"] == _basic_header("forgejo-secret")


@pytest.mark.parametrize("host", ["github", "forgejo"])
def test_missing_host_token_leaves_git_unconfigured(host):
    environment = {"TASK_RUNNER_GIT_HOST": host}
    if host == "forgejo":
        environment["TASK_RUNNER_GIT_HOST_BASE_URL"] = "https://forgejo.example.com"

    assert git_environment(environment) == {}


def test_unknown_git_host_fails_startup():
    with pytest.raises(ValueError, match="TASK_RUNNER_GIT_HOST"):
        git_environment({"TASK_RUNNER_GIT_HOST": "unknown"})


def test_missing_git_host_leaves_standalone_runner_unconfigured():
    assert git_environment({}) == {}


def test_forgejo_base_url_rejects_embedded_credentials():
    with pytest.raises(ValueError, match="without credentials"):
        git_environment(
            {
                "TASK_RUNNER_GIT_HOST": "forgejo",
                "TASK_RUNNER_GIT_HOST_BASE_URL": "https://user@example.com",
                "FORGEJO_TOKEN": "secret",
            }
        )


def _basic_header(token: str) -> str:
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"AUTHORIZATION: basic {encoded}"
