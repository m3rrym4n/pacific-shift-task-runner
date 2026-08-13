import base64
import io
import json

import pytest

from codex_runner.configure_git import git_environment


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _github_response(profile=None):
    profile = profile or {"id": 26526425, "login": "m3rrym4n"}

    def open_url(request, timeout):
        assert request.full_url == "https://api.github.com/user"
        assert request.get_header("Authorization") == "Bearer secret"
        assert timeout == 20
        return _Response(json.dumps(profile).encode())

    return open_url


def test_github_git_environment_uses_github_token():
    result = git_environment(
        {"TASK_RUNNER_GIT_HOST": "github", "GITHUB_TOKEN": "secret"},
        _github_response(),
    )

    assert result["GIT_CONFIG_COUNT"] == "3"
    assert result["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert result["GIT_CONFIG_VALUE_0"] == _basic_header("secret")
    assert result["GIT_CONFIG_KEY_1"] == "user.name"
    assert result["GIT_CONFIG_VALUE_1"] == "m3rrym4n"
    assert result["GIT_CONFIG_KEY_2"] == "user.email"
    assert result["GIT_CONFIG_VALUE_2"] == "26526425+m3rrym4n@users.noreply.github.com"


@pytest.mark.parametrize(
    "profile,error",
    [
        ({"id": "1740212", "login": "m3rrym4n"}, "numeric id"),
        ({"id": 26526425, "login": ""}, "valid login"),
    ],
)
def test_github_git_environment_rejects_invalid_authenticated_profile(profile, error):
    with pytest.raises(ValueError, match=error):
        git_environment(
            {"TASK_RUNNER_GIT_HOST": "github", "GITHUB_TOKEN": "secret"},
            _github_response(profile),
        )


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
