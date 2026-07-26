#!/usr/bin/env python3
"""Turn the codex-runner version endpoint response into workflow outputs."""

import argparse
import json
import re
from pathlib import Path


VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("response", type=Path)
    parser.add_argument("--installed-override", default="")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.response.read_text())
    installed = args.installed_override or payload["installed"]
    latest = payload["latest"]

    if not isinstance(installed, str) or not VERSION_PATTERN.fullmatch(installed):
        raise ValueError("installed Codex version must use x.y.z format")
    if not isinstance(latest, str) or not VERSION_PATTERN.fullmatch(latest):
        raise ValueError("latest Codex version must use x.y.z format")

    outputs = (
        f"installed={installed}\n"
        f"latest={latest}\n"
        f"drift_detected={'true' if installed != latest else 'false'}\n"
    )
    if args.github_output:
        with args.github_output.open("a") as output:
            output.write(outputs)
    else:
        print(outputs, end="")


if __name__ == "__main__":
    main()
