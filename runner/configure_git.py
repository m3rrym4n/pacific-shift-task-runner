import base64
import os
import shlex
from urllib.parse import urlsplit


def git_environment(environment: dict[str, str]) -> dict[str, str]:
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
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"http.{config_url}.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {credential}",
    }


def main() -> None:
    for key, value in git_environment(dict(os.environ)).items():
        print(f"export {key}={shlex.quote(value)}")


if __name__ == "__main__":
    main()
