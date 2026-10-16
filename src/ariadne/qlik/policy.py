"""Which tenant operations Ariadne may perform. Checked under every call.

An API key cannot be scoped by Qlik, so these rules live here, below the
tools. HTTP: GET always; POST, PUT and PATCH only when writes are enabled;
DELETE never; and only paths on a short allowlist. Engine: an allowlist of
read methods, a second allowlist of write methods, and nothing else, so no
Destroy* method and no data-fetching method can ever be sent. A session
object may only be one of the metadata list types, so GetLayout can never
carry data pages.

Metadata only, by construction: there is no mode in which Ariadne reads
data rows.
"""
from __future__ import annotations

import urllib.parse

HTTP_READ = frozenset({"GET"})
HTTP_WRITE = frozenset({"POST", "PUT", "PATCH"})
HTTP_NEVER = frozenset({"DELETE"})

ENGINE_READ = frozenset({
    "OpenDoc", "GetAppLayout", "GetScript", "CreateSessionObject", "GetLayout",
    "DestroySessionObject",   # a session object is not a tenant entity
    "GetMeasure", "GetDimension", "GetProperties", "GetTablesAndKeys",
    "GetObject", "GetFullPropertyTree", "GetLineage", "GetVariableByName",
})
ENGINE_WRITE = frozenset({"CreateMeasure", "CreateDimension", "SetProperties", "DoSave", "SetScript",
                          "CreateObject", "CreateChild"})

# A persisted object (a sheet, a chart) is created with its definition; the
# definition may hold a hypercube, which is metadata. What it may not hold
# is a data fetch, which would make a later GetLayout carry rows.
_DATA_FETCH_KEYS = frozenset({"qInitialDataFetch", "qInitialDataFetchStack"})

# The only session objects Ariadne creates: metadata listings. A hypercube or
# a list object would return data pages through GetLayout, so the definition
# of every CreateSessionObject is checked against this.
SESSION_LIST_TYPES = frozenset({"qMeasureList", "qDimensionList", "qVariableList", "qFieldList",
                                "SheetList", "MeasureList", "DimensionList"})
SESSION_DEF_KEYS = frozenset({"qInfo", "qMeasureListDef", "qDimensionListDef", "qVariableListDef",
                              "qFieldListDef", "qAppObjectListDef"})

# REST paths Ariadne may touch. Nothing here returns data rows; the export
# body (a .qvf) stays in the local process and never enters a tool result.
REST_PATH_PREFIXES = ("/api/v1/items", "/api/v1/apps", "/api/v1/spaces", "/api/v1/users",
                      "/api/v1/lineage-graphs", "/api/v1/temp-contents",
                      "/api/v1/data-sets", "/api/v1/glossaries", "/api/v1/reloads", "/api/v1/automations")

# Sub-resources under an allowed prefix that return data rows or values. A
# dataset's metadata and schema are allowed; its profile and sample are not.
import re as _re
REST_PATH_DENY = _re.compile(r"/(profiles?|samples?|preview|data|rows|values|selections|field-values|export-data)(/|$)")


class PolicyError(RuntimeError):
    """The call was refused by Ariadne's own policy, before reaching the tenant."""


def check_path(url_or_path: str) -> None:
    path = urllib.parse.urlsplit(url_or_path).path if "://" in (url_or_path or "") else (url_or_path or "").split("?")[0]
    if not any(path == pre or path.startswith(pre + "/") or path.startswith(pre + "?") for pre in REST_PATH_PREFIXES):
        raise PolicyError(f"REST path {path!r} is not on Ariadne's allowlist; it is not called.")
    if REST_PATH_DENY.search(path):
        raise PolicyError(f"REST path {path!r} returns data rows or values; it is not called.")


def check_http(method: str, allow_write: bool, url_or_path: str = "") -> None:
    if url_or_path:
        check_path(url_or_path)
    m = (method or "GET").upper()
    if m in HTTP_READ:
        return
    if m in HTTP_NEVER:
        raise PolicyError(f"HTTP {m} is never sent by Ariadne: nothing is deleted.")
    if m in HTTP_WRITE:
        if not allow_write:
            raise PolicyError(f"HTTP {m} refused: writes are disabled (ARIADNE_ALLOW_WRITE is not 1).")
        return
    raise PolicyError(f"HTTP {m} is not a method Ariadne uses.")


def check_session_def(params) -> None:
    d = params[0] if isinstance(params, list) and params else params
    if not isinstance(d, dict):
        raise PolicyError("CreateSessionObject without a definition is not sent.")
    qtype = ((d.get("qInfo") or {}).get("qType") or "")
    if qtype not in SESSION_LIST_TYPES:
        raise PolicyError(f"session object type {qtype!r} is not a metadata listing; not created.")
    extra = set(d) - SESSION_DEF_KEYS
    if extra:
        raise PolicyError(f"session object definition carries {sorted(extra)}; only metadata listings are created.")


def _has_data_fetch(node, depth=0) -> bool:
    if depth > 30:
        return False
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _DATA_FETCH_KEYS and v:
                return True
            if _has_data_fetch(v, depth + 1):
                return True
    elif isinstance(node, list):
        return any(_has_data_fetch(v, depth + 1) for v in node)
    return False


def check_object_def(params) -> None:
    d = params[0] if isinstance(params, list) and params else params
    if not isinstance(d, dict) or not (d.get("qInfo") or {}).get("qType"):
        raise PolicyError("an object is created only with a typed definition.")
    if _has_data_fetch(d):
        raise PolicyError("an object definition with an initial data fetch is not created: it would make "
                          "GetLayout carry data rows.")


def check_engine(method: str, allow_write: bool, params=None) -> None:
    if method == "CreateSessionObject":
        check_session_def(params)
    if method in ("CreateObject", "CreateChild", "SetProperties"):
        if method != "SetProperties" or _has_data_fetch(params):
            check_object_def(params)
    if method in ENGINE_READ:
        return
    if method in ENGINE_WRITE:
        if not allow_write:
            raise PolicyError(f"Engine {method} refused: writes are disabled (ARIADNE_ALLOW_WRITE is not 1).")
        return
    raise PolicyError(f"Engine {method} is not on Ariadne's allowlist; it is not sent.")
