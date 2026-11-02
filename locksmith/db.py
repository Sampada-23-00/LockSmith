"""Embedded Postgres for the benchmark. No install needed: pgserver ships the binaries."""
import shutil
import subprocess
import time
from pathlib import Path

import pgserver
import psycopg

ROOT = Path(__file__).resolve().parent.parent
PGDATA = ROOT / "pgdata"


STARTUP_WAIT_S = 120


def server():
    # cleanup_mode=None keeps the server running between scripts.
    # After an unclean shutdown (sleep, reboot) Postgres runs crash recovery, which on Windows
    # takes ~35s because it waits on its own open log file. pgserver doesn't wait, so we do.
    deadline = time.monotonic() + STARTUP_WAIT_S
    while True:
        try:
            return pgserver.get_server(PGDATA, cleanup_mode=None)
        except AssertionError:
            if time.monotonic() > deadline:
                break
            time.sleep(3)
    # Still not up: every run rebuilds its tables, so the data is disposable. Start fresh.
    _stop_stale_postgres()
    shutil.rmtree(PGDATA)
    return pgserver.get_server(PGDATA, cleanup_mode=None)


def _stop_stale_postgres() -> None:
    pid_file = PGDATA / "postmaster.pid"
    if pid_file.exists():
        pid = pid_file.read_text().splitlines()[0].strip()
        subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True)
        time.sleep(1)


def uri() -> str:
    return server().get_uri()


def connect() -> psycopg.Connection:
    return psycopg.connect(uri())


def log_dir() -> Path:
    return PGDATA / "log"
