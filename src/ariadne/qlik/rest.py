"""Qlik Cloud REST: the few calls Ariadne needs, with the behaviours a tenant
sweep depends on.

Everything goes through `request_json`: it asks for gzip (86% less on the
wire, measured on a lineage graph), retries 429/503 on Retry-After, and does
NOT retry a 404 - most apps have no lineage graph, so a miss must cost one
call. The tenant throttles harder than it looks: ~21 lineage calls back to
back were answered with a 429 on the 21st.
"""
from __future__ import annotations

import gzip
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from ..config import normalize_host


class QlikHTTPError(RuntimeError):
    def __init__(self, code: int, url: str, body: str = ""):
        super().__init__(f"HTTP {code} on {url.split('?')[0]}" + (f": {body[:160]}" if body else ""))
        self.code = code
        self.url = url


def request_json(url: str, api_key: str, timeout: int = 30, retries: int = 3):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}",
                                               "Accept-Encoding": "gzip"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if "gzip" in (resp.headers.get("Content-Encoding") or "").lower():
                    try:
                        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                    except OSError:
                        pass    # a proxy that decompressed but left the header on
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries:
                ra = e.headers.get("Retry-After") if e.headers else None
                try:
                    wait = float(ra) if ra else 1.5 * (attempt + 1)
                except (TypeError, ValueError):
                    wait = 1.5 * (attempt + 1)
                time.sleep(min(wait, 10))
                continue
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                body = ""
            raise QlikHTTPError(e.code, url, body) from None
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise


class Rest:
    """Bound to one tenant and one key."""

    def __init__(self, tenant: str, api_key: str):
        self.host = normalize_host(tenant)
        self.api_key = api_key

    def get(self, path: str, timeout: int = 30):
        url = path if path.startswith("http") else f"https://{self.host}{path}"
        return request_json(url, self.api_key, timeout)

    def paged(self, path: str, timeout: int = 30):
        url = path
        while url:
            data = self.get(url, timeout)
            yield data
            url = ((data.get("links", {}) or {}).get("next") or {}).get("href") or ""

    # ---------------------------------------------------------------- apps
    def list_apps(self):
        """Every app this key can list via the Items API, sorted by name.
        See app_from_item for the tri-state fields. Apps in another user's
        Personal space are not listed at all (the Items API is scoped by
        space membership) - a blind spot every caller must state."""
        apps = []
        for data in self.paged("/api/v1/items?resourceType=app&limit=100"):
            for it in data.get("data", []):
                a = app_from_item(it)
                if a:
                    apps.append(a)
        apps.sort(key=lambda a: (a["name"] or "").lower())
        return apps

    def app(self, guid: str) -> dict:
        return self.get(f"/api/v1/apps/{guid}")

    def app_scripts(self, guid: str) -> dict:
        return self.get(f"/api/v1/apps/{guid}/scripts")

    # -------------------------------------------------------------- spaces
    def list_spaces(self):
        """{space_id: {name, type, owner_id}}. type is what the Spaces API
        reports ('shared', 'managed', 'data'); an unexpected or missing value
        is kept as "" and read as unknown. Personal apps have no spaceId and
        never appear here."""
        spaces = {}
        for data in self.paged("/api/v1/spaces?limit=100"):
            for it in data.get("data", []):
                sid = it.get("id")
                if sid:
                    spaces[sid] = {"name": it.get("name", sid), "type": (it.get("type") or ""),
                                   "owner_id": it.get("ownerId") or ""}
        return spaces

    # --------------------------------------------------------------- users
    def me(self) -> dict:
        """Who this key acts as. Never raises: "" ids mean the tenant did not say."""
        try:
            d = self.get("/api/v1/users/me")
            return {"id": d.get("id") or "", "name": d.get("name") or "",
                    "roles": list(d.get("roles") or []), "status": d.get("status") or ""}
        except Exception:
            return {"id": "", "name": "", "roles": [], "status": ""}

    def user_info(self, uid: str) -> dict:
        """{"name", "status", "found"}: found is True when the tenant returned
        the user, False on a 404 (no such user, or one this key may not see),
        None when the lookup failed for any other reason - which says nothing
        about the user at all."""
        try:
            d = self.get(f"/api/v1/users/{uid}")
            return {"name": d.get("name") or "", "status": (d.get("status") or "").lower(),
                    "found": True}
        except QlikHTTPError as e:
            if e.code == 404:
                return {"name": "", "status": "", "found": False}
            return {"name": "", "status": "", "found": None}
        except Exception:
            return {"name": "", "status": "", "found": None}

    # ------------------------------------------------------------- lineage
    def lineage_graph(self, app_guid: str, level: str = "all", up: int = -1,
                      collapse: bool = False) -> dict:
        """Qlik's lineage-graphs API for an app -> {nodes: {qri: meta}, edges}.
        collapse defaults to False because True silently drops QVD nodes
        (measured: 150 vs 116 on one real app, 5 of 16 producers lost)."""
        qri = "qri:app:sense://" + app_guid
        enc = urllib.parse.quote(qri, safe="")
        url = (f"https://{self.host}/api/v1/lineage-graphs/nodes/{enc}"
               f"?level={level}&up={up}&collapse={'true' if collapse else 'false'}")
        data = request_json(url, self.api_key, timeout=120)
        graphs = []
        if isinstance(data.get("graph"), dict):
            graphs = [data["graph"]]
        elif isinstance(data.get("graphs"), dict):
            graphs = data["graphs"].get("graphs", []) or []
        elif isinstance(data.get("graphs"), list):
            graphs = data["graphs"]
        nodes, edges = {}, []
        for g in graphs:
            raw = g.get("nodes")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    raw = {}
            if isinstance(raw, dict):
                for nid, val in raw.items():
                    meta = dict(val.get("metadata", {})) if isinstance(val, dict) else {}
                    meta["label"] = (val.get("label", "") if isinstance(val, dict) else "") or meta.get("label", "")
                    nodes[nid] = meta
            for e in g.get("edges", []) or []:
                edges.append({"source": e.get("source"), "target": e.get("target"),
                              "relation": e.get("relation", []) or []})
        return {"nodes": nodes, "edges": edges}


def app_from_item(it: dict):
    """One Items API record -> app dict, or None without an id.
    `reloaded` is an ISO timestamp or "" and `published` is True/False/None:
    "" and None mean "the tenant did not report it", never "never" or "no".
    Qlik has moved these fields between API versions, so each is read from
    the places it has been seen and left blank when absent."""
    guid = it.get("resourceId") or it.get("id")
    if not guid:
        return None
    attrs = it.get("resourceAttributes") or {}
    meta = it.get("meta") or {}
    if "published" in attrs:
        v = attrs.get("published")
        published = v if isinstance(v, bool) or v is None else bool(v)
    elif attrs.get("publishTime") or meta.get("isPublished"):
        published = True
    else:
        published = None
    return {
        "name": it.get("name", "(unnamed)"),
        "guid": guid,
        "space_id": it.get("spaceId") or "",
        "owner_id": (it.get("ownerId") or attrs.get("ownerId") or ""),
        "reloaded": (attrs.get("lastReloadTime") or it.get("updatedAt") or ""),
        "published": published,
        "description": (it.get("description") or attrs.get("description") or "")[:500],
    }


def node_kind(qri: str, meta: dict) -> str:
    """A coarse kind for a lineage node, from its QRI and metadata."""
    q = (qri or "").lower()
    t = (meta.get("type") or "").upper()
    fp = (meta.get("filePath") or meta.get("label") or "").lower()
    if q.startswith("qri:app:"):
        return "app"
    if q.startswith("qri:qdf:") or fp.endswith(".qvd") or t == "QVD":
        return "qvd"
    if q.startswith("qri:db:") or t in ("DATABASE", "TABLE", "SQL"):
        return "database"
    if "qri:df:" in q or fp.endswith((".csv", ".xlsx", ".xls", ".txt", ".parquet")):
        return "file"
    if q.startswith("qri:dataset:") or t == "DATASET":
        return "dataset"
    return "other"


def guid_from_qri(qri: str) -> str:
    q = qri or ""
    if "sense://" in q:
        return q.split("sense://", 1)[1].split("#")[0].split("/")[0]
    return ""
