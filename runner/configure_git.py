import base64
import json
import os
import shlex
from typing import Any, Callable
from urllib.request import Request, urlopen
from urllib.parse import urlsplit


def _github_identity(
    token: str,
    open_url: Callable[..., Any] = urlopen,
) -> tuple[str, str]:
    request = Request(
        "https://api.github.com/user",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "variflex-runner",
        },
    )
    with open_url(request, timeout=20) as response:
        profile = json.load(response)

    account_id = profile.get("id")
    login = profile.get("login")
    if not isinstance(account_id, int) or isinstance(account_id, bool) or account_id <= 0:
        raise ValueError("GitHub /user response did not contain a valid numeric id")
    if not isinstance(login, str) or not login.strip():
        raise ValueError("GitHub /user response did not contain a valid login")

    return login, f"{account_id}+{login}@users.noreply.github.com"


def git_environment(
    environment: dict[str, str],
    open_url: Callable[..., Any] = urlopen,
) -> dict[str, str]:
    host = environment.get("TASK_RUNNER_GIT_HOST")
    if not host:
        return {}
    if host == "github":
        base_url = "https://github.com"
        token = environment.get("GITHUB_TOKEN")
    elif host == "forgejo":
        base_url = environment.get("TASK_RUNNER_GIT_HOST_BASE_URL", "")
        token = environment.get("FORGEJO_TOKEN")
    else:
        raise ValueError("TASK_RUNNER_GIT_HOST must be 'github' or 'forgejo'")

    if not token:
        return {}

    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
        raise ValueError("git host base URL must be an HTTP(S) URL without credentials")
    credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    config_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}/"
    result = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"http.{config_url}.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {credential}",
    }
    if host == "github":
        name, email = _github_identity(token, open_url)
        result.update(
            {
                "GIT_CONFIG_COUNT": "3",
                "GIT_CONFIG_KEY_1": "user.name",
                "GIT_CONFIG_VALUE_1": name,
                "GIT_CONFIG_KEY_2": "user.email",
                "GIT_CONFIG_VALUE_2": email,
            }
        )
    return result


def main() -> None:
    for key, value in git_environment(dict(os.environ)).items():
        print(f"export {key}={shlex.quote(value)}")


if __name__ == "__main__":
    main()
