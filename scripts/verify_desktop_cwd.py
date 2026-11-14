#!/usr/bin/env python3
"""Reproduce packaged sidecar cwd failure without touching operator data/processes.

Usage: python3 scripts/verify_desktop_cwd.py /absolute/path/to/atc-server --output /tmp/evidence
Runs the unchanged packaged binary first from /, then from disposable HOME.
Refuses to run if 8420 is occupied; terminates only its own process group.
"""

import argparse
import json
import os
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8420)) == 0:
            raise SystemExit("Refusing: port 8420 already has a listener")
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix="atc-desktop-cwd-") as temporary:
        home = Path(temporary)
        # Existing legacy DB must be reused, not replaced by an empty data store.
        with sqlite3.connect(home / "atc.db") as db:
            db.execute("CREATE TABLE legacy_startup_sentinel(value TEXT)")
            db.execute("INSERT INTO legacy_startup_sentinel VALUES ('preserved')")
        config = "backup:\n  auto_backup_enabled: false\n"
        (home / "config.local.yaml").write_text(config)
        env = {
            "HOME": str(home),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "TMPDIR": str(home),
            "TMUX_TMPDIR": str(home),
        }
        for name, cwd in (("finder-root", Path("/")), ("explicit-home", home)):
            healthy = False
            with (args.output / f"{name}.log").open("w") as log:
                process = subprocess.Popen(
                    [str(binary)],
                    cwd=cwd,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    deadline = time.monotonic() + 30
                    while process.poll() is None and time.monotonic() < deadline:
                        try:
                            with urllib.request.urlopen(
                                "http://127.0.0.1:8420/api/health", timeout=1
                            ) as response:
                                healthy = json.load(response)["status"] in ("ok", "degraded")
                            if healthy:
                                break
                        except (OSError, urllib.error.URLError):
                            time.sleep(0.2)
                    result = {
                        "case": name,
                        "healthy": healthy,
                        "exit_before_cleanup": process.poll(),
                    }
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=5)
                results.append(result)
        with sqlite3.connect(home / "atc.db") as db:
            preserved = db.execute("SELECT value FROM legacy_startup_sentinel").fetchone() == (
                "preserved",
            )
        assert (home / "config.local.yaml").read_text() == config
    report = {"binary": str(binary), "results": results, "legacy_database_preserved": preserved}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    assert not results[0]["healthy"], "Expected rc22 root-cwd reproduction"
    assert results[1]["healthy"] and preserved


if __name__ == "__main__":
    main()
