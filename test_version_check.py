import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parent / ".github/scripts/check-version-drift.py"


def run_check(tmp_path: Path, installed: str, latest: str, override: str = "") -> dict[str, str]:
    response = tmp_path / "response.json"
    response.write_text(json.dumps({"installed": installed, "latest": latest}))
    command = [sys.executable, str(SCRIPT), str(response)]
    if override:
        command.extend(["--installed-override", override])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


def test_no_drift_skips_build(tmp_path):
    assert run_check(tmp_path, "0.145.0", "0.145.0") == {
        "installed": "0.145.0",
        "latest": "0.145.0",
        "drift_detected": "false",
    }


def test_deliberately_stale_override_detects_drift(tmp_path):
    assert run_check(tmp_path, "0.145.0", "0.146.0", override="0.1.0") == {
        "installed": "0.1.0",
        "latest": "0.146.0",
        "drift_detected": "true",
    }


def test_invalid_override_is_rejected(tmp_path):
    with pytest.raises(subprocess.CalledProcessError):
        run_check(tmp_path, "0.145.0", "0.146.0", override="not-a-version")
