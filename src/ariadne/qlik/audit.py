"""Append-only audit log of what Ariadne changed: one JSON line per write
tool call, in a file you configure. Never the key, never a script body.
A real write (dry_run=false) requires the log to be configured; a dry run
does not."""
from __future__ import annotations

import datetime
import json
import os
import threading

_lock = threading.Lock()


class AuditLog:
    def __init__(self, path: str, mode: str = "writes"):
        self.path = (path or "").strip()
        self.mode = mode if mode in ("writes", "all", "off") else "writes"

    @property
    def enabled(self) -> bool:
        return bool(self.path) and self.mode != "off"

    def record(self, *, tool: str, user: dict, app_id: str, dry_run: bool, summary: dict,
               note: str = "", snapshot_ids=()) -> dict | None:
        if not self.enabled:
            return None
        if dry_run and self.mode != "all":
            return None
        entry = {
            "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "user_id": (user or {}).get("id", ""), "user": (user or {}).get("name", ""),
            "tool": tool, "app_id": app_id, "dry_run": bool(dry_run),
            "summary": summary, "developer_note": note, "snapshot_ids": list(snapshot_ids),
        }
        line = json.dumps(entry, ensure_ascii=False)
        with _lock:
            d = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(d, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return entry

    def read(self, limit: int = 100, app_id: str = "") -> list:
        if not self.path or not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if app_id and e.get("app_id") != app_id:
                    continue
                out.append(e)
        return out[-max(1, limit):]
