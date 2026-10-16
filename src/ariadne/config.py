"""Settings and the one secret.

Everything comes from the environment, because an MCP server is started by a
client (Claude Desktop, Claude Code, an agent runtime) that passes env, and a
settings file on disk would be a second place a key can leak from.

    QLIK_TENANT          tenant host, e.g. acme.eu.qlikcloud.com
    QLIK_API_KEY         an API key, or
    QLIK_API_KEY_FILE    a file holding one (first line)
    ARIADNE_WORKERS      concurrent engine sessions for bulk reads (default 6)
    ARIADNE_SCRIPT_MAX_CHARS  how much of a load script a tool returns at once
    ARIADNE_ALLOW_WRITE  "1" to enable the (future) write tools; off by default

The key is stripped where it enters, because a key pasted with a trailing
newline passes a connection test and then fails every request with
`Invalid header value` (a real defect in the application this was ported
from). `scrub()` removes the key from any text that is about to leave the
process - a log line, an error message, a tool result.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    tenant: str
    api_key: str
    workers: int = 6
    script_max_chars: int = 60_000
    allow_write: bool = False

    @property
    def host(self) -> str:
        return normalize_host(self.tenant)


def normalize_host(tenant: str) -> str:
    return (tenant or "").replace("https://", "").replace("wss://", "").strip().strip("/")


def _read_key(env: dict) -> str:
    key = (env.get("QLIK_API_KEY") or "").strip()
    if key:
        return key
    path = (env.get("QLIK_API_KEY_FILE") or "").strip()
    if path:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            return (f.readline() or "").strip()
    return ""


def load_settings(env: dict | None = None) -> Settings:
    env = os.environ if env is None else env
    tenant = normalize_host(env.get("QLIK_TENANT") or "")
    key = _read_key(env)
    missing = [n for n, v in (("QLIK_TENANT", tenant), ("QLIK_API_KEY or QLIK_API_KEY_FILE", key)) if not v]
    if missing:
        raise ConfigError("Ariadne is not configured: set " + " and ".join(missing)
                          + " in the MCP server's environment.")
    try:
        workers = max(1, int(env.get("ARIADNE_WORKERS") or 6))
    except ValueError:
        workers = 6
    try:
        max_chars = max(1_000, int(env.get("ARIADNE_SCRIPT_MAX_CHARS") or 60_000))
    except ValueError:
        max_chars = 60_000
    return Settings(tenant=tenant, api_key=key, workers=workers,
                    script_max_chars=max_chars,
                    allow_write=(env.get("ARIADNE_ALLOW_WRITE") or "") == "1")


def scrub(text: str, api_key: str) -> str:
    """Remove the key - raw, URL-encoded or repr-escaped - from outbound text."""
    if not text or not api_key:
        return text or ""
    out = str(text)
    for form in (api_key, api_key.replace("\n", "\\n"), re.escape(api_key)):
        out = out.replace(form, "<api key>")
    # a core of the key (JWT body) in case the error carries it partially
    core = api_key.strip()[:24]
    if len(core) >= 16 and core in out:
        out = out.replace(core, "<api key>")
    return out
