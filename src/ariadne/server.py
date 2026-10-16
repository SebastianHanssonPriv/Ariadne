"""The MCP surface: every tool, one envelope, no analysis.

Tools are thin. They fetch through `backend`, hand the result to the pure
functions in `ariadne.qlik`, and wrap the answer in `envelope`. Anything
that can be partial reports its coverage in the result; anything the tenant
refused is an `access` finding with the rule and the governed fix; the key
never reaches a result or a log.

The backend is injectable so the whole surface can be exercised against a
fake tenant in tests, and so a future transport (streamable HTTP, a shared
service) changes nothing here.
"""
from __future__ import annotations

import concurrent.futures
import functools
import threading
import time
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__
from .config import ConfigError, Settings, load_settings, scrub
from .envelope import (SCRIPT_FIX, SCRIPT_RULE, coverage, envelope, failed, refused)
from .qlik import bulk
from .qlik import diff as D
from .qlik import snapshot as SN
from .qlik import writes as W
from .qlik.policy import PolicyError
from .qlik import expressions as X
from .qlik import ownership as O
from .qlik import script as S
from .qlik.engine import EngineError, EngineRefused, EngineSession
from .qlik.rest import QlikHTTPError, Rest, guid_from_qri, node_kind

PERSONAL_BLIND_SPOT = ("Apps in another user's Personal space are not listed by the Items "
                       "API and cannot be seen by this key; counts are of what this key can "
                       "list, not of the tenant.")
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                            idempotent_hint=True, open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False,
                        idempotent_hint=False, open_world_hint=True)
WRITE_OVERWRITES = ToolAnnotations(read_only_hint=False, destructive_hint=True,
                                   idempotent_hint=False, open_world_hint=True)
MAX_COMPARE_APPS = 25
CACHE_TTL = 300


class Backend:
    """Everything that touches a tenant, behind one object."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.rest = Rest(settings.tenant, settings.api_key, allow_write=settings.allow_write)
        self.facts = bulk.FactsCache()
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def engine(self, app_id: str) -> EngineSession:
        return EngineSession(self.settings.tenant, self.settings.api_key, app_id,
                             allow_write=self.settings.allow_write)

    def invalidate(self, key: str = ""):
        with self._lock:
            if key:
                self._cache.pop(key, None)
            else:
                self._cache.clear()

    def read_script(self, app_id: str):
        return bulk.read_script(self.settings.tenant, self.settings.api_key, app_id)

    def cached(self, key: str, fn):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < CACHE_TTL:
                return hit[1]
        val = fn()
        with self._lock:
            self._cache[key] = (time.time(), val)
        return val

    def apps(self):
        return self.cached("apps", self.rest.list_apps)

    def spaces(self):
        return self.cached("spaces", self.rest.list_spaces)

    def me(self):
        return self.cached("me", self.rest.me)

    def facts_for(self, apps, on_progress=None):
        return bulk.facts_for_apps(self.settings.tenant, self.settings.api_key, apps,
                                   cache=self.facts, workers=self.settings.workers,
                                   me_id=(self.me() or {}).get("id", ""), on_progress=on_progress)


def build_server(backend: Backend | None = None, settings_env: dict | None = None) -> MCPServer:
    state = {"backend": backend}

    def be() -> Backend:
        if state["backend"] is None:
            state["backend"] = Backend(load_settings(settings_env))
        return state["backend"]

    def key() -> str:
        b = state["backend"]
        return b.settings.api_key if b else ""

    def guarded(what: str):
        """Turn the ways a tenant call can fail into findings, not tracebacks."""
        def deco(fn):
            @functools.wraps(fn)
            def wrapper(*a, **kw):
                try:
                    return fn(*a, **kw)
                except ConfigError as e:
                    return failed(what, str(e))
                except PolicyError as e:
                    return refused(what, "Ariadne policy: nothing is deleted, writes need "
                                   "ARIADNE_ALLOW_WRITE=1, and a published app is snapshotted "
                                   "before it is changed.", "See docs/DESIGN.md, write policy.",
                                   scrub(str(e), key()))
                except EngineRefused as e:
                    return refused(what, SCRIPT_RULE, SCRIPT_FIX, scrub(str(e), key()))
                except QlikHTTPError as e:
                    if e.code in (401, 403):
                        return refused(what, "The tenant refused this key for this call "
                                       f"(HTTP {e.code}).", "Check the key's user roles and the "
                                       "space roles of the object; diagnose_app_access shows "
                                       "which layer refuses.", scrub(str(e), key()))
                    if e.code == 404:
                        return failed(what, scrub(f"not found or not visible to this key ({e})", key()))
                    return failed(what, scrub(str(e), key()))
                except (EngineError, OSError, ValueError) as e:
                    return failed(what, scrub(str(e)[:300], key()))
            return wrapper
        return deco

    srv = MCPServer(
        "ariadne", version=__version__,
        instructions=(
            "Ariadne answers governance questions about a Qlik Cloud tenant: who owns what, "
            "where a QVD comes from and who reads it, whether two apps define the same measure "
            "the same way, what an app's load script actually does. Every result is an envelope "
            "with data, coverage, unknowns, limitations, evidence and a confidence set from "
            "coverage. Read coverage and unknowns before stating a conclusion: a None value "
            "means the tenant did not report it, never 'no'; an access.state of 'refused' is a "
            "finding about permissions, not evidence about the object. Results are prioritised "
            "worklists to verify. Tenant-wide tools (trace_qvd, list_extractors, "
            "check_ownership, compare_master_items) take minutes on large tenants; narrow with "
            "space_id or max_apps when you can. Use the official Qlik MCP server for data "
            "questions, selections, chart building and writes; Ariadne is read-only."),
    )

    # ------------------------------------------------------------ helpers
    def _space_label(spaces, space_id):
        if not space_id:
            return "Personal", "personal"
        sp = spaces.get(space_id) or {}
        return sp.get("name") or space_id, (sp.get("type") or "unknown")

    def _select_apps(space_id: str = "", max_apps: int = 0):
        apps = be().apps()
        if space_id:
            apps = [a for a in apps if a.get("space_id") == space_id]
        total = len(apps)
        if max_apps and max_apps > 0:
            apps = apps[:max_apps]
        return apps, total

    def _tenant_coverage(cov: dict, total: int, scope: str):
        c = coverage(cov["asked"], cov["answered"], cov["refused"], cov["failed"], scope,
                     cache_hits=cov.get("cache_hits", 0), listed=total)
        if cov.get("systemic_stop"):
            c["systemic_stop"] = cov["systemic_stop"]
        if cov.get("failure_kinds"):
            c["failure_kinds"] = [{"error": scrub(k, key()), "count": n} for k, n in cov["failure_kinds"]]
        return c

    def _script_limitations(cov: dict):
        lim = [PERSONAL_BLIND_SPOT]
        if cov["refused"]:
            lim.append(f"{cov['refused']} script(s) refused. {SCRIPT_RULE} {SCRIPT_FIX}")
        if cov.get("systemic_stop"):
            lim.append("Stopped early: every attempt failed the same permanent way and none "
                       "succeeded, so the rest were not tried. This is about the key, not the apps.")
        lim.append("A name built at run time ($(...) after variable resolution) is reported as "
                   "unresolved, never matched; an $(Include=...) file's contents are not read.")
        return lim

    # ================================================================ REACH
    @srv.tool(annotations=READ_ONLY)
    @guarded("tenant overview")
    def tenant_overview() -> dict:
        """What this key can see on the tenant: who it acts as, spaces by type, apps by
        space type, how many apps report publish state and reload time, and the standing
        blind spots. Call first; it is cheap and sets the coverage for everything after."""
        b = be()
        me, spaces, apps = b.me(), b.spaces(), b.apps()
        by_space_type, pub = {}, {"published": 0, "not_published": 0, "not_reported": 0}
        reload_missing, owners = 0, set()
        for a in apps:
            _, st = _space_label(spaces, a.get("space_id"))
            by_space_type[st] = by_space_type.get(st, 0) + 1
            p = a.get("published")
            pub["published" if p is True else "not_published" if p is False else "not_reported"] += 1
            if not a.get("reloaded"):
                reload_missing += 1
            if a.get("owner_id"):
                owners.add(a["owner_id"])
        space_types = {}
        for s in spaces.values():
            t = s.get("type") or "unknown"
            space_types[t] = space_types.get(t, 0) + 1
        unknowns = []
        if pub["not_reported"]:
            unknowns.append(f"publish state not reported for {pub['not_reported']} app(s)")
        if reload_missing:
            unknowns.append(f"last reload time not reported for {reload_missing} app(s)")
        if not me.get("id"):
            unknowns.append("the tenant did not say which user this key acts as")
        data = {"acting_as": {"id": me.get("id", ""), "name": me.get("name", ""),
                              "roles": me.get("roles", [])},
                "spaces": {"count": len(spaces), "by_type": space_types},
                "apps": {"count": len(apps), "by_space_type": by_space_type,
                         "publish_state": pub, "distinct_owners": len(owners)},
                "script_access_rule": SCRIPT_RULE}
        return envelope(data, coverage=coverage(len(apps), len(apps), scope="apps this key can list"),
                        unknowns=unknowns, limitations=[PERSONAL_BLIND_SPOT])

    @srv.tool(annotations=READ_ONLY)
    @guarded("list apps")
    def list_apps(space_id: str = "", name_contains: str = "", owner_id: str = "",
                  published: str = "any", limit: int = 100, offset: int = 0) -> dict:
        """Apps this key can list, with owner, space, publish state (true/false/null =
        not reported) and last reload. Filter by space_id, a case-insensitive name
        fragment, owner_id, or published = 'true' | 'false' | 'unknown' | 'any'. Paged."""
        b = be()
        spaces = b.spaces()
        apps = b.apps()
        q = (name_contains or "").lower()
        rows = []
        for a in apps:
            if space_id and a.get("space_id") != space_id:
                continue
            if q and q not in (a.get("name") or "").lower():
                continue
            if owner_id and a.get("owner_id") != owner_id:
                continue
            p = a.get("published")
            if published == "true" and p is not True:
                continue
            if published == "false" and p is not False:
                continue
            if published == "unknown" and p is not None:
                continue
            name, st = _space_label(spaces, a.get("space_id"))
            rows.append({**a, "space": name, "space_type": st})
        page = rows[max(0, offset):max(0, offset) + max(1, min(limit, 500))]
        unknown_pub = sum(1 for r in rows if r["published"] is None)
        unknowns = ([f"publish state not reported for {unknown_pub} of {len(rows)} matching app(s)"]
                    if unknown_pub else [])
        return envelope({"apps": page, "matching": len(rows), "offset": offset,
                         "next_offset": (offset + len(page)) if offset + len(page) < len(rows) else None},
                        coverage=coverage(len(apps), len(apps), scope="apps this key can list",
                                          matching=len(rows), returned=len(page)),
                        unknowns=unknowns, limitations=[PERSONAL_BLIND_SPOT])

    @srv.tool(annotations=READ_ONLY)
    @guarded("describe app")
    def describe_app(app_id: str, include_model: bool = True) -> dict:
        """One app: REST attributes (owner and their account status, space and its type,
        publish time, last reload), and with include_model the data model (tables with row
        counts, field count, master item and sheet counts) plus whether this key can read
        the load script. A refused script is reported as an access finding, not as empty."""
        b = be()
        body = b.rest.app(app_id)
        attrs = body.get("attributes", {}) or {}
        spaces = b.spaces()
        space_name, space_type = _space_label(spaces, attrs.get("spaceId") or "")
        owner_id = attrs.get("ownerId") or ""
        owner = b.rest.user_info(owner_id) if owner_id else {"name": "", "status": "", "found": None}
        okind, olabel = O.classify(owner)
        unknowns, limitations, evidence = [], [PERSONAL_BLIND_SPOT], []
        if not owner_id:
            unknowns.append("ownerId not reported")
        if owner.get("found") is None:
            unknowns.append("owner account status could not be verified (lookup failed or no status)")
        data = {
            "id": app_id, "name": attrs.get("name", ""), "description": (attrs.get("description") or "")[:1000],
            "space": {"id": attrs.get("spaceId") or "", "name": space_name, "type": space_type},
            "owner": {"id": owner_id, "name": owner.get("name", ""), "status": owner.get("status", ""),
                      "state": okind, "label": olabel},
            "published": bool(attrs.get("publishTime")) if attrs.get("publishTime") else None,
            "publish_time": attrs.get("publishTime") or "",
            "last_reload": attrs.get("lastReloadTime") or "",
            "modified": attrs.get("modifiedDate") or "", "created": attrs.get("createdDate") or "",
            "has_section_access": attrs.get("hasSectionAccess"),
            "usage": attrs.get("usage") or "",
        }
        if data["published"] is None:
            unknowns.append("publishTime not reported: publish state unknown, not 'unpublished'")
        asked, answered, refused_n = 1, 1, 0
        if include_model:
            asked += 1
            with b.engine(app_id) as eng:
                try:
                    tables = eng.tables()
                    fields = eng.model_fields()
                    data["model"] = {
                        "tables": tables, "table_count": len(tables),
                        "field_count": len(fields),
                        "key_fields": sum(1 for f in fields if f["is_key"]),
                        "system_fields": sum(1 for f in fields if f["is_system"]),
                        "measures": len(eng.measures()), "dimensions": len(eng.dimensions()),
                        "variables": len(eng.variables()), "sheets": len(eng.sheets()),
                    }
                    answered += 1
                except EngineRefused as e:
                    refused_n += 1
                    data["model"] = None
                    data["model_access"] = {"state": "refused", "detail": scrub(str(e), key())}
                asked += 1
                try:
                    script = eng.get_script() or ""
                    data["script"] = {"readable": True, "lines": len(script.splitlines()),
                                      "chars": len(script),
                                      "include_files": S.include_refs(script)}
                    answered += 1
                    evidence.append("load script read via Engine GetScript")
                except EngineRefused as e:
                    refused_n += 1
                    data["script"] = {"readable": False, "lines": None, "chars": None}
                    data["script_access"] = {"state": "refused", "rule": SCRIPT_RULE, "fix": SCRIPT_FIX,
                                             "detail": scrub(str(e), key())}
                    limitations.append("The load script was refused; nothing here says what the app "
                                       "loads or stores.")
        return envelope(data, coverage=coverage(asked, answered, refused=refused_n, scope=f"app {app_id}"),
                        unknowns=unknowns, limitations=limitations, evidence=evidence)

    @srv.tool(annotations=READ_ONLY)
    @guarded("diagnose app access")
    def diagnose_app_access(app_id: str) -> dict:
        """Which layer lets this key see an app and which refuses: the Items listing, the
        direct REST lookup, ownership versus this key's user, the privileges the tenant
        reports, an Engine session, the load script, and the REST scripts endpoint. Costs
        a handful of calls and answers what a tenant-wide scan would answer 1,900 times."""
        b = be()
        checks = []

        def rec(name, ok, detail):
            checks.append({"check": name, "ok": ok, "detail": scrub(str(detail), key())})

        try:
            apps = b.apps()
            found = next((a for a in apps if a["guid"] == app_id), None)
            rec("Items API listing (space-membership scoped)", bool(found),
                f"found: '{found['name']}', spaceId '{found['space_id'] or '(none = Personal)'}'"
                if found else f"not among the {len(apps)} app(s) this key can list")
        except Exception as e:
            rec("Items API listing (space-membership scoped)", False, f"call failed: {e}")
        me = b.me()
        try:
            body = b.rest.app(app_id)
            attrs = body.get("attributes", {}) or {}
            rec("Direct REST lookup (/api/v1/apps/{id})", True,
                f"found: '{attrs.get('name', '')}', spaceId '{attrs.get('spaceId') or '(none = Personal)'}', "
                f"publishTime '{attrs.get('publishTime', '')}'")
            owner = attrs.get("ownerId") or ""
            if me.get("id") and owner:
                rec("Ownership (owner vs this key's user)", owner == me["id"],
                    "this key's user owns the app" if owner == me["id"]
                    else f"owned by another user ({owner}); this key is {me['id']}")
            else:
                rec("Ownership (owner vs this key's user)", None,
                    "skipped: the tenant did not report one of the two ids")
            privs = body.get("privileges") or attrs.get("privileges") or []
            rec("Privileges the tenant reports on this app", bool(privs),
                ", ".join(str(x) for x in privs) if privs else "none reported by this endpoint")
        except QlikHTTPError as e:
            rec("Direct REST lookup (/api/v1/apps/{id})", False, f"refused or missing: {e}")
        except Exception as e:
            rec("Direct REST lookup (/api/v1/apps/{id})", False, f"call failed: {e}")
        try:
            with b.engine(app_id) as eng:
                title = eng.layout().get("qTitle", app_id)
                rec("Engine session (OpenDoc)", True, f"opened: '{title}'")
                try:
                    script = eng.get_script() or ""
                    rec("Engine load script (GetScript)", True, f"read {len(script.splitlines())} line(s)")
                except EngineRefused as e:
                    rec("Engine load script (GetScript)", False, f"refused: {e}")
        except Exception as e:
            rec("Engine session (OpenDoc)", False, f"could not open: {e}")
        try:
            body = b.rest.app_scripts(app_id)
            n = len(body.get("data") or []) if isinstance(body, dict) else 0
            rec("REST load script (/api/v1/apps/{id}/scripts)", True,
                f"returned {n} script version(s)" if n else "answered, no versions listed")
        except Exception as e:
            rec("REST load script (/api/v1/apps/{id}/scripts)", False, f"refused: {e}")
        items_ok = checks[0]["ok"]
        rest_ok = next((c["ok"] for c in checks if c["check"].startswith("Direct REST")), None)
        open_ok = next((c["ok"] for c in checks if c["check"].startswith("Engine session")), None)
        script_ok = next((c["ok"] for c in checks if c["check"].startswith("Engine load script")), None)
        rest_script_ok = next((c["ok"] for c in checks if c["check"].startswith("REST load script")), None)
        if open_ok and script_ok is False:
            conclusion = ("The app opens but its script is refused. " + SCRIPT_RULE + " " + SCRIPT_FIX)
        elif open_ok and script_ok:
            conclusion = "This key can open the app and read its script; tenant-wide scans will cover it."
        elif not items_ok and rest_ok:
            conclusion = ("Visible by direct lookup but not in the listing: the app is probably in a "
                          "space this key is not a member of (or another user's Personal space).")
        elif not items_ok and not rest_ok:
            conclusion = "Not visible to this key by any route; nothing can be said about the app."
        else:
            conclusion = "The app could not be opened; the script check says nothing about permissions."
        if rest_script_ok and script_ok is False:
            conclusion += (" NOTE: the REST scripts endpoint answered where the Engine refused; "
                           "worth acting on, since script reads could move to REST.")
        asked = len(checks)
        answered = sum(1 for c in checks if c["ok"] is not None)
        return envelope({"checks": checks, "conclusion": conclusion},
                        coverage=coverage(asked, answered, scope=f"app {app_id}"),
                        evidence=[c["detail"] for c in checks])

    # =============================================================== THREAD
    @srv.tool(annotations=READ_ONLY)
    @guarded("get load script")
    def get_load_script(app_id: str, offset: int = 0, max_chars: int = 0) -> dict:
        """The app's load script, as text, paged by character offset so a long script does not
        flood the context. Credential-looking values in CONNECT strings are masked. A refused
        script is an access finding with the rule and the governed fix, not an empty script."""
        b = be()
        limit = max_chars if max_chars and max_chars > 0 else b.settings.script_max_chars
        with b.engine(app_id) as eng:
            script = eng.get_script() or ""
        clean = S.redact_secrets(script)
        chunk = clean[offset:offset + limit]
        nxt = offset + len(chunk) if offset + len(chunk) < len(clean) else None
        return envelope({"script": chunk, "offset": offset, "next_offset": nxt,
                         "total_chars": len(clean), "total_lines": len(clean.splitlines()),
                         "truncated": nxt is not None,
                         "include_files": S.include_refs(script)},
                        coverage=coverage(1, 1, scope=f"app {app_id}",
                                          chars_returned=len(chunk), chars_total=len(clean)),
                        limitations=(["Credential-looking values are masked as <redacted>."]
                                     + (["An $(Include=...) file's contents are not part of this text."]
                                        if S.include_refs(script) else [])),
                        evidence=["read via Engine GetScript"])

    @srv.tool(annotations=READ_ONLY)
    @guarded("analyze load script")
    def analyze_load_script(app_id: str = "", script_text: str = "") -> dict:
        """What a load script does, as facts: the tables it builds (label, source kind,
        source, connection, fields), the QVDs it stores and reads, names it builds at run
        time (unresolved, never guessed), whether it loads from outside Qlik (true/false/
        null = cannot tell), and whether that makes it an extractor. Comments are not code;
        $(vVar) paths are resolved where the variable is SET to a literal. Give app_id to read
        the script from the tenant, or script_text to analyse text you already have."""
        b = None
        if script_text:
            script, source = script_text, "script_text"
        elif app_id:
            b = be()
            with b.engine(app_id) as eng:
                script = eng.get_script() or ""
            source = f"app {app_id}"
        else:
            return failed("analyze load script", "give app_id or script_text")
        tables = S.parse_load_tables(script)
        facts = S.facts_from_script(script)
        reason = S.extractor_reason(facts)
        includes = S.include_refs(script)
        has_binary = bool(S.re.search(r"(?im)^\s*binary\b", S.strip_script_comments(script)))
        data = {
            "lines": len(script.splitlines()),
            "tables": [{"label": t["label"], "kind": t["kind"], "source": S.redact_secrets(t["source"]),
                        "connection": t["connection"], "files": sorted(t["files"]),
                        "fields": [f["out"] for f in t["out_fields"]], "wildcard": t["wildcard"],
                        "external": t["external"]} for t in tables],
            "stores_qvd": sorted(facts["stores"]), "reads_files": sorted(facts["reads"]),
            "unresolved_names": sorted(facts["unresolved"]),
            "loads_external": facts["loads_external"], "source_kind": facts["source_kind"],
            "is_extractor": bool(reason) if facts["source_kind"] != "unread" else None,
            "extractor_evidence": reason, "binary_load": has_binary,
            "connections": S.connections_named(script), "include_files": includes,
        }
        unknowns = []
        if facts["loads_external"] is None:
            unknowns.append("loads_external is null: file-based sources this parser cannot classify (review)")
        if facts["unresolved"]:
            unknowns.append(f"{len(facts['unresolved'])} file name(s) are built at run time and cannot "
                            "be known from the text")
        limitations = ["Text parsing: a $(=...) or LET-built expression is not evaluated.",
                       "Comments and REM blocks are stripped before parsing; commented-out STORE/LOAD "
                       "lines do not count."]
        if includes:
            limitations.append(f"{len(includes)} $(Include) file(s) are not read; what they load or "
                               "store is not in these facts.")
        if has_binary:
            limitations.append("BINARY load: the model comes from another app; its sources are not here.")
        return envelope(data, coverage=coverage(1, 1, scope=source),
                        unknowns=unknowns, limitations=limitations,
                        evidence=[f"classified on behaviour: {reason}" if reason else
                                  f"not an extractor: {facts['source_kind']}, stores {len(facts['stores'])} QVD(s)"])

    @srv.tool(annotations=READ_ONLY)
    @guarded("lineage graph")
    def get_lineage_graph(app_id: str, max_nodes: int = 300) -> dict:
        """Qlik's own lineage graph for an app, whole, not one step at a time: nodes by kind
        (apps, QVDs, files, databases, datasets), the upstream apps, and the QVDs. Fetched
        uncollapsed on purpose: the collapsed graph silently drops QVD nodes. A 404 means the
        tenant holds no graph for this app, which says nothing about what the app loads."""
        b = be()
        g = b.rest.lineage_graph(app_id)
        nodes = []
        kinds = {}
        for qri, meta in g["nodes"].items():
            k = node_kind(qri, meta)
            kinds[k] = kinds.get(k, 0) + 1
            nodes.append({"qri": qri, "kind": k, "label": meta.get("label") or meta.get("filePath") or "",
                          "app_id": guid_from_qri(qri) if k == "app" else ""})
        nodes.sort(key=lambda n: (n["kind"], n["label"].lower()))
        upstream_apps = sorted({n["app_id"] for n in nodes if n["kind"] == "app" and n["app_id"] and n["app_id"] != app_id})
        data = {"node_count": len(nodes), "edge_count": len(g["edges"]), "nodes_by_kind": kinds,
                "upstream_apps": upstream_apps,
                "qvds": sorted({n["label"] for n in nodes if n["kind"] == "qvd"}),
                "nodes": nodes[:max(1, max_nodes)], "edges": g["edges"][:max(1, max_nodes) * 2]}
        lim = ["Lineage graphs are built by Qlik from reload metadata; an app never reloaded under "
               "lineage, or loading via a path lineage does not track, has an incomplete graph."]
        if len(nodes) > max_nodes:
            lim.append(f"nodes truncated to {max_nodes} of {len(nodes)}; counts above are over all nodes")
        return envelope(data, coverage=coverage(1, 1, scope=f"lineage graph of app {app_id}"),
                        limitations=lim, evidence=["lineage-graphs API, level=all, collapse=false"])

    def _facts_scan(space_id: str, max_apps: int):
        b = be()
        apps, total = _select_apps(space_id, max_apps)
        facts, cov, errors = b.facts_for(apps)
        return b, apps, total, facts, cov, errors

    @srv.tool(annotations=READ_ONLY)
    @guarded("trace QVD")
    def trace_qvd(qvd_name: str, space_id: str = "", max_apps: int = 0) -> dict:
        """Which apps write a QVD and which read it, from every load script this key can
        read: the producer/consumer map Qlik's one-step lineage does not give. The name is
        matched on its lower-cased basename after resolving $(vVar) paths. Coverage says
        how many scripts were read, refused and failed; an app that builds QVD names at run
        time is listed as a possible reader, never as a confirmed one. Tenant-wide: minutes
        on a large tenant; narrow with space_id or max_apps."""
        base = (qvd_name or "").strip().lower().replace("\\", "/").split("/")[-1]
        if not base.endswith(".qvd"):
            base += ".qvd"
        b, apps, total, facts, cov, errors = _facts_scan(space_id, max_apps)
        spaces = b.spaces()
        producers, consumers, possible, unread = [], [], [], []
        stem = base[:-4]
        near = set()
        for a in apps:
            f = facts.get(a["guid"]) or S.UNREADABLE
            row = {"app": a["name"], "app_id": a["guid"], "space": _space_label(spaces, a.get("space_id"))[0],
                   "last_reload": a.get("reloaded") or ""}
            if f["source_kind"] == "unread":
                unread.append({**row, "error": scrub(errors.get(a["guid"], ""), key())})
                continue
            if base in f["stores"]:
                producers.append({**row, "source_kind": f["source_kind"]})
            if base in f["reads"]:
                consumers.append(row)
            if f["unresolved"]:
                possible.append({**row, "unresolved_names": sorted(f["unresolved"])[:10]})
            for n in f["stores"] | f["reads"]:
                if stem and stem in n and n != base:
                    near.add(n)
        c = _tenant_coverage(cov, total, f"load scripts of {len(apps)} app(s)")
        unknowns = []
        if unread:
            unknowns.append(f"{len(unread)} app(s) whose script could not be read may write or read "
                            f"{base}; nothing is known about them")
        if possible:
            unknowns.append(f"{len(possible)} app(s) build file names at run time and might read or "
                            f"write {base}")
        data = {"qvd": base, "producers": producers, "consumers": consumers,
                "possible_runtime_references": possible[:50],
                "unread_apps": unread[:50], "unread_count": len(unread),
                "similar_names": sorted(near)[:25]}
        ev = [f"{len(producers)} producer(s) by STORE ... INTO, {len(consumers)} consumer(s) by file "
              f"reference, over {cov['answered']} readable script(s) of {len(apps)}"]
        return envelope(data, coverage=c, unknowns=unknowns, limitations=_script_limitations(cov), evidence=ev)

    @srv.tool(annotations=READ_ONLY)
    @guarded("list extractors")
    def list_extractors(space_id: str = "", max_apps: int = 0) -> dict:
        """Apps that load data from outside Qlik AND store a QVD, classified on what their
        script does, never on their name (real extractors are called 'QVD creator'). Each
        row carries its evidence. Apps whose script was refused are counted as unknown,
        not as 'not an extractor'. Tenant-wide: narrow with space_id or max_apps."""
        b, apps, total, facts, cov, errors = _facts_scan(space_id, max_apps)
        spaces = b.spaces()
        rows, unknown, review = [], 0, []
        for a in apps:
            f = facts.get(a["guid"]) or S.UNREADABLE
            if f["source_kind"] == "unread":
                unknown += 1
                continue
            reason = S.extractor_reason(f)
            row = {"app": a["name"], "app_id": a["guid"], "space": _space_label(spaces, a.get("space_id"))[0],
                   "owner_id": a.get("owner_id", ""), "last_reload": a.get("reloaded") or "",
                   "stores": sorted(f["stores"]), "source_kind": f["source_kind"]}
            if reason:
                rows.append({**row, "evidence": reason})
            elif f["stores"] and f["loads_external"] is None:
                review.append({**row, "why": "stores QVDs from file sources this parser cannot classify"})
        rows.sort(key=lambda r: (-len(r["stores"]), r["app"].lower()))
        c = _tenant_coverage(cov, total, f"load scripts of {len(apps)} app(s)")
        unknowns = ([f"{unknown} app(s) could not be classified: script not readable"] if unknown else [])
        if review:
            unknowns.append(f"{len(review)} app(s) store QVDs from sources the parser could not classify")
        return envelope({"extractors": rows, "review": review[:100], "unknown_count": unknown,
                         "qvds_produced": sum(len(r["stores"]) for r in rows)},
                        coverage=c, unknowns=unknowns, limitations=_script_limitations(cov),
                        evidence=["extractor = loads from outside Qlik (SQL or non-QVD file) and STOREs a QVD"])

    # =============================================================== GOVERN
    def _master_items(eng: EngineSession):
        return eng.measures(), eng.dimensions(), eng.variables()

    @srv.tool(annotations=READ_ONLY)
    @guarded("get master items")
    def get_master_items(app_id: str, include_reserved_variables: bool = False) -> dict:
        """Master measures (expression, label, format, tags), master dimensions (fields,
        grouping) and variables (definition, script-created or not) of one app."""
        b = be()
        with b.engine(app_id) as eng:
            m, d, v = _master_items(eng)
        if not include_reserved_variables:
            v = [x for x in v if not x.get("is_reserved")]
        return envelope({"measures": m, "dimensions": d, "variables": v,
                         "counts": {"measures": len(m), "dimensions": len(d), "variables": len(v)}},
                        coverage=coverage(1, 1, scope=f"app {app_id}"))

    @srv.tool(annotations=READ_ONLY)
    @guarded("compare master items")
    def compare_master_items(app_ids: list[str]) -> dict:
        """Across several apps: master items that share a NAME but not a calculation
        (consistency risk), and calculations that appear under several NAMES (redundancy).
        The comparison key folds case, whitespace and comments outside string literals and
        treats [Sales], "Sales" and Sales as one field, but keeps whitespace inside a literal
        and keeps 'X' (a string) apart from "X" (a field). $(var) references are expanded
        from each app's own variables where they are plain definitions. Up to 25 apps."""
        b = be()
        ids = [x for x in (app_ids or []) if x][:MAX_COMPARE_APPS]
        if len(ids) < 2:
            return failed("compare master items", "give at least two app_ids")
        apps_by_id = {a["guid"]: a for a in b.apps()}
        measures, dims, read, refused_n, failures = [], [], 0, 0, []
        for aid in ids:
            title = (apps_by_id.get(aid) or {}).get("name") or aid
            try:
                with b.engine(aid) as eng:
                    m, d, v = _master_items(eng)
            except EngineRefused as e:
                refused_n += 1
                failures.append({"app_id": aid, "app": title, "error": scrub(str(e), key())})
                continue
            except (EngineError, OSError) as e:
                failures.append({"app_id": aid, "app": title, "error": scrub(str(e)[:200], key())})
                continue
            read += 1
            varmap = {x["name"]: x["definition"] for x in v if x.get("name") and not x.get("is_reserved")}
            for x in m:
                measures.append({"name": x["name"], "expression": x["expression"], "app": title,
                                 "expr_expanded": X.expand_vars(x["expression"], varmap)})
            for x in d:
                dims.append({"name": x["name"], "definition": x["fields"], "app": title,
                             "def_expanded": X.expand_vars(x["fields"], varmap)})
        res = X.analyze_consistency(measures, dims)
        data = {**res, "apps_compared": read, "apps_failed": failures,
                "items_compared": {"measures": len(measures), "dimensions": len(dims)}}
        lim = ["Two expressions that differ only by a variable the parser could not expand "
               "($(=...), parameterised) are reported as different.",
               "Same key means same text after normalisation, not proven same result."]
        if failures:
            lim.append(f"{len(failures)} app(s) could not be read; their items are absent from every "
                       "group, so a conflict with them cannot be seen.")
        return envelope(data, coverage=coverage(len(ids), read, refused=refused_n,
                                                failed=len(failures) - refused_n, scope=f"{len(ids)} app(s)"),
                        limitations=lim,
                        evidence=["comparison key: case, whitespace and comments folded outside literals"])

    @srv.tool(annotations=READ_ONLY)
    @guarded("find unused in app")
    def find_unused_in_app(app_id: str, include_all: bool = False) -> dict:
        """Candidates to remove from one app: model fields, master items and variables that
        nothing on any sheet, master item or variable references. Every expression on every
        object is read (colour expressions, calc conditions, sort expressions, extension
        properties). A field named only in a comment or a string literal does not count as
        used; a bare name that is also a function name does, so the list under-reports.
        Dynamic $(=...) expressions hide usage and are listed; verify before deleting."""
        b = be()
        with b.engine(app_id) as eng:
            m, d, v = _master_items(eng)
            objects = eng.objects()
            fields = eng.model_fields()
        res = X.analyze_usage(m, d, v, objects, fields)
        dyn = res["dynamic"]
        data = {
            "unused_fields": res["fields"]["unused"],
            "unused_master_items": res["master"]["unused"],
            "unused_variables": res["variables"]["unused"],
            "dynamic_expressions": dyn[:100],
            "counts": {"fields": len(res["fields"]["all"]), "unused_fields": len(res["fields"]["unused"]),
                       "master_items": len(res["master"]["all"]),
                       "unused_master_items": len(res["master"]["unused"]),
                       "variables": len(res["variables"]["all"]),
                       "unused_variables": len(res["variables"]["unused"]),
                       "objects_read": len(objects), "dynamic_expressions": len(dyn)},
        }
        if include_all:
            data["all_fields"] = res["fields"]["all"]
            data["all_master_items"] = res["master"]["all"]
        lim = ["Usage is matched on names in expressions; a field used only through a $(=...) "
               "expression evaluated at run time can read as unused.",
               "Objects on sheets this key cannot open are not read.",
               "Usage from outside the app (another app binary-loading it, a reporting tool, an "
               "automation) is invisible here."]
        hidden = sum(1 for x in dyn if "HIDES" in x["type"])
        unknowns = ([f"{hidden} $(=...) expression(s) are evaluated at run time and may reference "
                     "fields this analysis cannot see"] if hidden else [])
        return envelope(data, coverage=coverage(1, 1, scope=f"app {app_id}", objects_read=len(objects)),
                        unknowns=unknowns, limitations=lim,
                        evidence=["corpus: master measures and dimensions, variables, every expression "
                                  "harvested from every sheet object's full property tree"])

    @srv.tool(annotations=READ_ONLY)
    @guarded("check ownership")
    def check_ownership() -> dict:
        """Apps and spaces whose owner is disabled, deleted, not found, or could not be
        verified, with a priority from what the tenant reports (published, managed space,
        recent reload; an unreported input scores 0, never a penalty), and how concentrated
        published content is on a few owners. A failed lookup is UNVERIFIED, never 'fine'."""
        b = be()
        apps, spaces = b.apps(), b.spaces()
        uids = sorted({a.get("owner_id") for a in apps if a.get("owner_id")}
                      | {s.get("owner_id") for s in spaces.values() if s.get("owner_id")})
        looked = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=b.settings.workers) as pool:
            for uid, info in zip(uids, pool.map(b.rest.user_info, uids)):
                looked[uid] = info
        res = O.scan_ownership(apps, spaces, looked)
        verified = sum(1 for i in looked.values() if i.get("found") is not None)
        unverified = len(uids) - verified
        unknowns = []
        if unverified:
            unknowns.append(f"{unverified} owner lookup(s) failed; their content is listed as unverified")
        if res["apps_without_owner_id"]:
            unknowns.append(f"{res['apps_without_owner_id']} app(s) reported no ownerId")
        return envelope(res, coverage=coverage(len(uids), verified, failed=unverified,
                                               scope=f"{len(uids)} distinct owner(s) of {len(apps)} app(s) "
                                                     f"and {len(spaces)} space(s)"),
                        unknowns=unknowns,
                        limitations=[PERSONAL_BLIND_SPOT,
                                     "A 404 on a user is evidence (no such user, or not visible to this "
                                     "key); any other failure is not."],
                        evidence=["Users API status passed through as reported"])

    # ================================================================ WRITE
    def _write_gate(what):
        b = be()
        if not b.settings.allow_write:
            return refused(what, "Writes are disabled on this server (ARIADNE_ALLOW_WRITE is not 1).",
                           "Enable writes only for the developers who hold the rights in Qlik; the key "
                           "decides what can be changed, Ariadne decides what is never deleted.")
        return None

    def _app_by_id(b, app_id):
        a = next((x for x in b.apps() if x["guid"] == app_id), None)
        if a is None:
            body = b.rest.app(app_id)
            attrs = body.get("attributes", {}) or {}
            a = {"name": attrs.get("name", ""), "guid": app_id, "space_id": attrs.get("spaceId") or "",
                 "owner_id": attrs.get("ownerId") or "", "reloaded": attrs.get("lastReloadTime") or "",
                 "published": None, "publish_time": attrs.get("publishTime") or "",
                 "origin_app_id": attrs.get("originAppId") or "",
                 "description": attrs.get("description") or ""}
        return a

    def _snapshot(b, app, explicit_archive, dry_run):
        """Run the snapshot rule for `app`. Returns (plan, taken, refusal_or_None)."""
        pl = SN.plan(app, b.apps(), b.spaces(), b.settings, explicit_archive)
        taken = []
        if pl.get("blocked"):
            return pl, taken, refused(f"snapshot of {app['guid']}", "Ariadne policy: the archive space "
                                      "must be one configured archive space and the routing rule must give "
                                      "one answer.", "Pass archive_space_id, or extend ARIADNE_ARCHIVE_MAP.",
                                      "; ".join(pl["blocked"]))
        if dry_run:
            return pl, taken, None
        for item in pl["items"]:
            if item["needed"]:
                taken.append(SN.execute(b.rest, app, item, app.get("description") or ""))
        if taken:
            b.invalidate("apps")
        return pl, taken, None

    @srv.tool(annotations=WRITE)
    @guarded("snapshot app")
    def snapshot_app(app_id: str, archive_space_id: str = "", dry_run: bool = True) -> dict:
        """Take the rollback snapshot of an app's published state: an export without data
        (unless the app is listed in ARIADNE_SNAPSHOT_WITH_DATA) imported into the archive
        space the routing rule selects, stamped so the next write after the same publish
        takes none. dry_run=True (the default) shows the plan and the chosen archive space
        and changes nothing. Pass archive_space_id to override the rule; it must be one of
        the configured archive spaces."""
        gate = _write_gate("snapshot app")
        if gate:
            return gate
        b = be()
        app = _app_by_id(b, app_id)
        pl, taken, refusal = _snapshot(b, app, archive_space_id, dry_run)
        if refusal:
            return refusal
        return envelope({"plan": pl, "taken": taken, "dry_run": dry_run},
                        coverage=coverage(1, 1, scope=f"app {app_id}"),
                        limitations=["A snapshot without data restores script, variables, master items "
                                     "and visuals; data comes back on reload from the pipelines."],
                        evidence=[pl["reason"]])

    @srv.tool(annotations=WRITE)
    @guarded("archive app")
    def archive_app(app_id: str, archive_space_id: str = "", dry_run: bool = True) -> dict:
        """Move an app into an archive space (the routing rule's choice, or archive_space_id if
        it is one of the configured archive spaces). The app is moved, never copied or
        deleted. dry_run=True shows the move without doing it."""
        gate = _write_gate("archive app")
        if gate:
            return gate
        b = be()
        app = _app_by_id(b, app_id)
        sp = b.spaces().get(app.get("space_id") or "") or {}
        target, why = SN.route(app.get("space_id") or "", sp.get("name") or "", b.settings, archive_space_id)
        if app.get("space_id") == target:
            return envelope({"moved": False, "reason": "already in that archive space", "space_id": target},
                            coverage=coverage(1, 1, scope=f"app {app_id}"))
        if not dry_run:
            b.rest.move_app(app_id, target)
            b.invalidate("apps")
            check = (b.rest.app(app_id).get("attributes") or {})
            if (check.get("spaceId") or "") != target:
                return failed("archive app", f"the tenant did not move {app_id}; it is in "
                              f"{check.get('spaceId') or 'Personal'}")
        return envelope({"moved": not dry_run, "dry_run": dry_run, "app": app["name"], "from_space": sp.get("name") or "Personal",
                         "to_space_id": target, "routing": why},
                        coverage=coverage(1, 1, scope=f"app {app_id}"))

    @srv.tool(annotations=READ_ONLY)
    @guarded("compare app versions")
    def compare_app_versions(app_id: str, other_app_id: str) -> dict:
        """What differs between two apps, typically a snapshot and the current source:
        script (unified diff), variables, master measures and dimensions (added, removed,
        changed on the comparison key), and sheet objects (added, removed, changed
        expressions). This is what a rollback to the snapshot would undo."""
        b = be()
        with b.engine(app_id) as ea:
            ba = ea.bundle()
        with b.engine(other_app_id) as eb:
            bb = eb.bundle()
        res = D.compare_bundles(ba, bb)
        return envelope(res, coverage=coverage(2, 2, scope=f"{app_id} vs {other_app_id}"),
                        limitations=["Objects are compared on their expressions, type, title and master item "
                                     "references, not on layout or styling.",
                                     "Data is not compared."])

    @srv.tool(annotations=WRITE_OVERWRITES)
    @guarded("apply master items")
    def apply_master_items(app_id: str, measures: list[dict] | None = None,
                           dimensions: list[dict] | None = None, mode: str = "upsert",
                           archive_space_id: str = "", dry_run: bool = True) -> dict:
        """Create or update master measures and dimensions in an app from definitions
        (measure: name, expression, label, label_expression, format_type, format_pattern,
        description, tags; dimension: name, fields, field_labels, grouping, label_expression,
        description, tags). Matches on the EXACT title; a near miss (case or padding) is
        refused and named; a title carried by several items is updated on all of them and
        says so. No delete mode exists. If the app has a published copy, its snapshot is
        taken first (see snapshot_app) and the write does not run if that fails. dry_run=True
        (the default) returns the exact actions and changes nothing."""
        gate = _write_gate("apply master items")
        if gate:
            return gate
        if mode not in W.MODES:
            return refused("apply master items", "Ariadne creates and updates master items; it never deletes.",
                           f"Use one of {W.MODES}.", f"mode={mode!r}")
        b = be()
        app = _app_by_id(b, app_id)
        pl, taken, refusal = _snapshot(b, app, archive_space_id, dry_run)
        if refusal:
            return refusal
        with b.engine(app_id) as eng:
            rm = W.apply_master(eng, "measure", measures or [], mode, dry_run) if measures else None
            rd = W.apply_master(eng, "dimension", dimensions or [], mode, dry_run) if dimensions else None
            changed = sum((r["counts"]["created"] + r["counts"]["updated"]) for r in (rm, rd) if r)
            saved = False
            if changed and not dry_run:
                eng.do_save()
                saved = True
        actions = (rm["actions"] if rm else []) + (rd["actions"] if rd else [])
        ambiguous = [a for a in actions if a["action"] == "AMBIGUOUS"]
        return envelope({"dry_run": dry_run, "snapshot": {"plan": pl, "taken": taken},
                         "measures": rm["counts"] if rm else None, "dimensions": rd["counts"] if rd else None,
                         "actions": actions, "saved": saved},
                        coverage=coverage(1, 1, scope=f"app {app_id}"),
                        unknowns=([f"{len(ambiguous)} definition(s) refused as ambiguous; fix the titles"]
                                  if ambiguous else []),
                        limitations=["A property the definition does not mention keeps its current value "
                                     "(shallow merge)."],
                        evidence=[f"{a['action']} {a['kind']} {a['name']!r}" for a in actions][:50])

    # ============================================================== PROMPTS
    @srv.prompt()
    def governance_review(space_id: str = "") -> str:
        """A tenant governance review in the order that keeps coverage honest."""
        scope = f" limited to space {space_id}" if space_id else ""
        return (f"Run a Qlik Cloud governance review{scope} with Ariadne.\n"
                "1. tenant_overview: state what this key can see and the blind spots.\n"
                "2. check_ownership: list content whose owner is gone or unverified, by priority.\n"
                "3. list_extractors: the apps that land data, with their evidence.\n"
                "4. For the three most-read QVDs you find, trace_qvd each.\n"
                "5. compare_master_items across the published apps in scope (max 25).\n"
                "Report findings first, then the coverage of each step as numbers, then unknowns and "
                "limitations verbatim. Never turn a None or a refused access into a 'no'. Frame the "
                "result as a prioritised worklist to verify.")

    @srv.resource("ariadne://principles", name="principles", mime_type="text/markdown")
    def principles() -> str:
        """The rules every Ariadne answer is built on."""
        return PRINCIPLES

    return srv


PRINCIPLES = """# Ariadne principles

1. Absence of evidence is never evidence. None means not known and survives into
   the answer as its own state. A refused call is a permission finding, not a fact
   about the object. A gap never becomes a penalty.
2. Say what you did not measure. Every partial scan reports coverage as counts in
   the result, and confidence is set from coverage, never from wording.
3. Classify on behaviour, not names. An extractor stores a QVD and loads from
   outside Qlik; it is not an app with a word in its title.
4. Comments are not code, strings are not code. Strip comments, resolve $(vVar)
   from literal SETs, and report a name still holding $( as unresolved.
5. Compute totals where the full data is; truncate only what is displayed.
6. Reports are worklists to verify, not verdicts. No text parser sees inside a
   dynamic expression, and the Admin APIs do not expose everything a chart does.
7. Permission is a finding. Opening an app and reading its script are different
   rights; ownership sorts the work and never skips an app on a guess.
8. The key never leaves the process: not in a result, not in a log, not in an error.
"""
