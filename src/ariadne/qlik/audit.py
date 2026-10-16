"""Append-only audit log of what Ariadne changed: one JSON line per write
tool call. Never the key, never a script body.

There is always a log. When ARIADNE_AUDIT_LOG is unset, the default is a
per-user file (%LOCALAPPDATA%\\Ariadne\\audit.jsonl on Windows,
~/.ariadne/audit.jsonl elsewhere), so a client configuration that forgets
the line still leaves a record on that machine. Point it at a per-user file
in a shared folder the BI team owns to make the record central. A real
write is refused when the log cannot be opened for appending (checked
before the write, so nothing changes unlogged); ARIADNE_AUDIT=off is the
only way to run without one, and that is a deliberate setting."""
from __future__ import annotations

import datetime
import json
import os
import threading

_lock = threading.Lock()


def default_path() -> str:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return os.path.join(base, "Ariadne", "audit.jsonl")
    return os.path.join(os.path.expanduser("~"), ".ariadne", "audit.jsonl")


class AuditLog:
    def __init__(self, path: str, mode: str = "writes"):
        self.mode = mode if mode in ("writes", "all", "off") else "writes"
        self.path = (path or "").strip() or (default_path() if self.mode != "off" else "")
        self.explicit = bool((path or "").strip())

    @property
    def enabled(self) -> bool:
        return bool(self.path) and self.mode != "off"

    def check_writable(self) -> str:
        """"" when an entry can be appended now, else the reason. Called
        before a real write so a write never happens without its record."""
        if not self.enabled:
            return "" if self.mode == "off" else "no audit log path"
        try:
            d = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(d, exist_ok=True)
            with open(self.path, "a", encoding="utf-8"):
                pass
            return ""
        except OSError as e:
            return f"audit log {self.path} cannot be opened for appending: {e}"

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
