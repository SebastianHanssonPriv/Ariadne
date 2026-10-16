"""Tenant-wide script reads and the facts cache.

Reading a script is one WebSocket handshake, one OpenDoc and one GetScript:
almost entirely network wait, and every app is independent, so this runs
`workers` sessions at once. `workers` is modest on purpose: the limit that
matters is the tenant's concurrent-session and rate limits.

Permission is a finding, not an error. Ownership only SORTS the work (owned
apps first, because they are the ones whose script a key can certainly
read); no app is skipped on a guess, because nothing in the listing reveals
who holds 'Can edit data in applications' in a space. The abort fires only
when the first SYSTEMIC_FAILURE_AFTER reads all failed the same permanent
way and none succeeded - and it must never pre-empt an app that would have
worked, which is why successes are counted in the worker thread.

The cache holds derived facts, not scripts: 27x smaller measured on a 1,909
app estate, and free of the LIB CONNECT lines that name service accounts.
Keyed on (guid, last reload, FACTS_VERSION), so a stale entry can never be
read; an app with no reload time has no safe key and is fetched every time.
"""
from __future__ import annotations

import concurrent.futures
import threading

from . import script as S
from .engine import EngineRefused, EngineSession, is_permanent_error

SYSTEMIC_FAILURE_AFTER = 30


class FactsCache:
    def __init__(self):
        self._d = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(app: dict):
        reloaded = app.get("reloaded") or ""
        if not reloaded:
            return None
        return (app.get("guid"), reloaded, S.FACTS_VERSION)

    def get(self, app: dict):
        k = self.key(app)
        if k is None:
            return None
        with self._lock:
            return self._d.get(k)

    def put(self, app: dict, facts: dict):
        k = self.key(app)
        if k is None:
            return
        with self._lock:
            self._d[k] = facts

    def __len__(self):
        return len(self._d)


def owned_first(apps, me_id: str):
    if not me_id:
        return list(apps)
    return sorted(apps, key=lambda a: (0 if a.get("owner_id") == me_id else 1,
                                       (a.get("name") or "").lower()))


def read_script(tenant, api_key, guid):
    """(script, error): script is None when it could not be read, and the
    error says why - 'refused' errors are permission findings."""
    for attempt in range(2):
        s = EngineSession(tenant, api_key, guid)
        try:
            s.connect()
            return s.get_script() or "", ""
        except EngineRefused as e:
            return None, f"refused: {e}"
        except Exception as e:
            msg = str(e)[:200]
            if attempt == 1 or is_permanent_error(msg):
                return None, msg
        finally:
            s.close()
    return None, "unknown"


def facts_for_apps(tenant, api_key, apps, cache: FactsCache | None = None,
                   workers: int = 6, me_id: str = "", on_progress=None):
    """{guid: facts} for every app, plus a coverage record. An unreadable
    app gets S.UNREADABLE - source_kind 'unread' - never an empty fact set.
    """
    cache = cache if cache is not None else FactsCache()
    out, errors = {}, {}
    todo = []
    hits = 0
    for a in apps:
        f = cache.get(a)
        if f is not None:
            out[a["guid"]] = f
            hits += 1
        else:
            todo.append(a)
    todo = owned_first(todo, me_id)
    state = {"ok": 0, "failed": 0, "systemic": None}
    tally = {}
    lock = threading.Lock()

    def stop_now():
        return bool(state["systemic"])

    def one(app):
        if stop_now():
            return None
        script, err = read_script(tenant, api_key, app["guid"])
        with lock:
            if script is None:
                state["failed"] += 1
                tally[err] = tally.get(err, 0) + 1
                errors[app["guid"]] = err
                if (state["systemic"] is None and state["ok"] == 0
                        and state["failed"] >= SYSTEMIC_FAILURE_AFTER
                        and all(is_permanent_error(m) for m in tally)):
                    state["systemic"] = max(tally, key=tally.get)
                return None
            state["ok"] += 1
        facts = S.facts_from_script(script)
        cache.put(app, facts)
        return facts

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(one, a): a for a in todo}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            a = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = None
                with lock:
                    state["failed"] += 1
                    errors[a["guid"]] = str(e)[:200]
            out[a["guid"]] = res if res is not None else dict(S.UNREADABLE)
            done += 1
            if on_progress:
                on_progress(done, len(todo))
            if stop_now():
                for f in futures:
                    f.cancel()
                for f, g in futures.items():
                    if g["guid"] not in out:
                        if f.done() and not f.cancelled():
                            try:
                                r = f.result()
                            except Exception:
                                r = None
                            out[g["guid"]] = r if r is not None else dict(S.UNREADABLE)
                        else:
                            out[g["guid"]] = dict(S.UNREADABLE)
                            errors[g["guid"]] = "not attempted: stopped after systemic failure"
                break

    refused = sum(1 for e in errors.values() if "refused" in e.lower() or is_permanent_error(e))
    cov = {
        "asked": len(apps), "answered": hits + state["ok"], "refused": refused,
        "failed": max(0, len(errors) - refused), "cache_hits": hits,
        "systemic_stop": state["systemic"] or "",
        "failure_kinds": sorted(tally.items(), key=lambda kv: -kv[1])[:6],
    }
    return out, cov, errors
