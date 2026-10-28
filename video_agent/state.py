from __future__ import annotations

import fcntl
import json
import os
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from .util import digest, emit, file_hash, read_json, write_json


class Paused(Exception):
    pass


class Store:
    """Content-addressed immutable stages; SQLite records runs and failures."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "state.sqlite3")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, started REAL, finished REAL, status TEXT, config_hash TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, run_id TEXT, stage TEXT, key TEXT, status TEXT, started REAL, finished REAL, error TEXT, artifact TEXT);
        """)
        self.db.commit()
        self.run_id = ""
        self.events: list[dict] = []

    @contextmanager
    def locked(self):
        with (self.root / "project.lock").open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another process is running this project") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def begin(self, config: dict) -> str:
        # The project lock guarantees previous 'running' entries are abandoned.
        self.db.execute("UPDATE attempts SET status='interrupted',finished=? WHERE status='running'", (time.time(),))
        self.db.execute("UPDATE runs SET status='interrupted',finished=? WHERE status='running'", (time.time(),))
        self.run_id = uuid.uuid4().hex
        self.db.execute("INSERT INTO runs VALUES(?,?,NULL,'running',?,NULL)", (self.run_id, time.time(), digest(config)))
        self.db.commit()
        return self.run_id

    def finish(self, status: str, error: str | None = None):
        self.db.execute("UPDATE runs SET status=?,error=?,finished=? WHERE id=?", (status, error, time.time(), self.run_id))
        self.db.commit()
        write_json(self.root / "last-run.json", {"run_id": self.run_id, "status": status, "error": error, "stages": self.events})

    def cached(self, folder: Path, key: str) -> dict | None:
        try:
            manifest = read_json(folder / "manifest.json")
            if manifest["key"] != key or not manifest.get("outputs"):
                return None
            for info in manifest["outputs"].values():
                p = (folder / info["path"]).resolve()
                if not p.is_relative_to(folder.resolve()) or not p.is_file() or p.stat().st_size != info["bytes"] or file_hash(p) != info["sha256"]:
                    return None
            return manifest
        except (OSError, KeyError, ValueError, TypeError):
            return None

    def step(self, stage: str, recipe: dict, action: Callable[[Path], dict[str, str]], *, fail_once: str | None = None, stop_after: str | None = None) -> dict:
        key = digest({"stage": stage, "recipe": recipe})
        folder = self.root / "artifacts" / key
        manifest = self.cached(folder, key)
        started = time.time()
        cursor = self.db.execute("INSERT INTO attempts(run_id,stage,key,status,started) VALUES(?,?,?,'running',?)", (self.run_id, stage, key, started))
        attempt_id = cursor.lastrowid
        self.db.commit()
        work = self.root / "staging" / f"{key}-{uuid.uuid4().hex}"
        emit("stage_started", stage=stage, cached=bool(manifest))
        try:
            if manifest is None:
                work.mkdir(parents=True)
                marker = self.root / "injected" / digest(stage)
                if stage == fail_once and not marker.exists():
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    marker.write_text("Controlled failure for acceptance testing; not a production media error.\n")
                    raise RuntimeError(f"Controlled one-time failure at {stage}")
                outputs = action(work)
                if not outputs:
                    raise RuntimeError(f"{stage} produced no declared outputs")
                info = {}
                for name, relative in outputs.items():
                    p = (work / relative).resolve()
                    if not p.is_relative_to(work.resolve()) or not p.is_file() or p.stat().st_size == 0:
                        raise RuntimeError(f"{stage} missing/invalid output: {relative}")
                    info[name] = {"path": relative, "bytes": p.stat().st_size, "sha256": file_hash(p)}
                manifest = {"key": key, "stage": stage, "recipe": recipe, "outputs": info}
                write_json(work / "manifest.json", manifest)
                folder.parent.mkdir(parents=True, exist_ok=True)
                if folder.exists():
                    shutil.rmtree(folder)  # Only an invalid cache with this exact key.
                os.replace(work, folder)
                status = "built"
            else:
                status = "cached"
            self.db.execute("UPDATE attempts SET status=?,finished=?,artifact=? WHERE id=?", (status, time.time(), str(folder), attempt_id))
            self.db.commit()
            row = {"stage": stage, "key": key, "status": status, "seconds": round(time.time()-started, 3)}
            self.events.append(row)
            emit("stage_completed", **row)
        except BaseException as exc:
            self.db.execute("UPDATE attempts SET status='failed',finished=?,error=?,artifact=? WHERE id=?", (time.time(), str(exc), str(work), attempt_id))
            self.db.commit()
            self.events.append({"stage": stage, "key": key, "status": "failed", "error": str(exc), "log_directory": str(work)})
            raise
        if stage == stop_after:
            raise Paused(f"Saved checkpoint after {stage}")
        return {"key": key, "folder": folder, "files": {name: folder / info["path"] for name, info in manifest["outputs"].items()}}

    def status(self) -> dict:
        self.db.row_factory = sqlite3.Row
        runs = [dict(r) for r in self.db.execute("SELECT * FROM runs ORDER BY started DESC LIMIT 10")]
        attempts = [dict(r) for r in self.db.execute("SELECT * FROM attempts ORDER BY id DESC LIMIT 100")]
        return {"runs": runs, "attempts": attempts}
