"""Qlik Engine API (JSON-RPC over WebSocket) for one app at a time.

A session keeps all state on the instance - its own socket, its own request
id - so many can run at once from a thread pool. Opening an app and reading
its load script are different rights (see envelope.SCRIPT_RULE); `get_script`
raises EngineRefused for the permission case so a caller can report it as a
finding rather than an error.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse

import websocket

from ..config import normalize_host
from .policy import check_engine
from .script import redact_inline
from .devnote import verify_insert

_PERMANENT = ("access denied", "forbidden", "unauthorized", "not authorized",
              "app not found", "resource not found")

# Keys whose string values are Qlik expressions. Matched rather than
# enumerated because extension objects invent their own property names.
_EXPR_KEYS = {"qdef", "qv", "qfielddefs"}
_EXPR_KEY_HINT = re.compile(r"(?i)expr|condition")


def proxy_settings(host: str) -> dict:
    """websocket-client keyword arguments from HTTPS_PROXY / https_proxy and
    NO_PROXY, the variables every corporate PC already carries for pip and
    the browser. Empty when no proxy applies."""
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    for entry in (e.strip().lower() for e in no_proxy.split(",") if e.strip()):
        if entry == "*" or host.lower() == entry or host.lower().endswith("." + entry.lstrip(".")) or host.lower().endswith(entry):
            return {}
    raw = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    if not raw:
        return {}
    u = urllib.parse.urlsplit(raw if "://" in raw else "http://" + raw)
    if not u.hostname:
        return {}
    kw = {"http_proxy_host": u.hostname, "http_proxy_port": u.port or 8080,
          "proxy_type": (u.scheme or "http").lower()}
    if u.username:
        kw["http_proxy_auth"] = (urllib.parse.unquote(u.username), urllib.parse.unquote(u.password or ""))
    return kw


class EngineError(RuntimeError):
    pass


class EngineRefused(EngineError):
    """The engine answered, and the answer was 'no' - a permission finding."""


def is_permanent_error(msg: str) -> bool:
    m = (msg or "").lower()
    return any(p in m for p in _PERMANENT)


class EngineSession:
    def __init__(self, tenant: str, api_key: str, app_id: str, timeout: int = 60,
                 allow_write: bool = False):
        self.host = normalize_host(tenant)
        self.api_key = api_key
        self.app_id = app_id
        self.timeout = timeout
        self.allow_write = allow_write
        self.id = 0
        self.ws = None
        self.app_h = None

    # ------------------------------------------------------------ plumbing
    def connect(self):
        """Open the WebSocket. urllib honours HTTPS_PROXY on its own; the
        websocket library does not, so the same variable is read here and
        passed through, or a corporate PC would reach REST and not the Engine."""
        kw = {"header": [f"Authorization: Bearer {self.api_key}"], "timeout": self.timeout}
        kw.update(proxy_settings(self.host))
        self.ws = websocket.create_connection(f"wss://{self.host}/app/{self.app_id}", **kw)
        self.ws.recv()  # OnConnected
        return self

    def call(self, handle, method, params):
        check_engine(method, self.allow_write, params)
        self.id += 1
        self.ws.send(json.dumps({"jsonrpc": "2.0", "id": self.id, "handle": handle,
                                 "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.id:
                if "error" in msg:
                    err = msg["error"]
                    text = f"{err.get('message', '')} (code {err.get('code', '')})" \
                        if isinstance(err, dict) else str(err)
                    if is_permanent_error(text):
                        raise EngineRefused(text)
                    raise EngineError(text)
                return msg["result"]

    def close(self):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    def open(self):
        if self.app_h is None:
            self.app_h = self.call(-1, "OpenDoc", [self.app_id])["qReturn"]["qHandle"]
        return self.app_h

    # ---------------------------------------------------------------- reads
    def layout(self) -> dict:
        return self.call(self.open(), "GetAppLayout", [])["qLayout"]

    def get_script(self) -> str:
        """The load script with INLINE table rows withheld: rows typed into a
        script are data, and nothing downstream of this method sees them."""
        return redact_inline(self.call(self.open(), "GetScript", [])["qScript"] or "")

    def _list(self, qtype: str, list_key: str, def_key: str, extra: dict | None = None):
        d = {"qInfo": {"qType": list_key}, def_key: {"qType": qtype}}
        if extra:
            d[def_key].update(extra)
        h = self.call(self.open(), "CreateSessionObject", [d])["qReturn"]["qHandle"]
        try:
            return self.call(h, "GetLayout", [])["qLayout"][list_key]["qItems"]
        finally:
            try:
                self.call(self.open(), "DestroySessionObject", [h])
            except Exception:
                pass

    def measures(self):
        rows = []
        for it in self._list("measure", "qMeasureList", "qMeasureListDef"):
            mid = it["qInfo"]["qId"]
            mh = self.call(self.open(), "GetMeasure", [mid])["qReturn"]["qHandle"]
            p = self.call(mh, "GetProperties", [])["qProp"]
            meta = p.get("qMetaDef", {})
            meas = p.get("qMeasure", {})
            fmt = meas.get("qNumFormat", {}) or {}
            rows.append({"id": mid, "name": meta.get("title", ""), "label": meas.get("qLabel", ""),
                         "label_expression": meas.get("qLabelExpression", ""),
                         "expression": meas.get("qDef", ""),
                         "description": meta.get("description", ""),
                         "tags": list(meta.get("tags", []) or []),
                         "format_type": fmt.get("qType", ""), "format_pattern": fmt.get("qFmt", "")})
        return rows

    def dimensions(self):
        rows = []
        for it in self._list("dimension", "qDimensionList", "qDimensionListDef"):
            did = it["qInfo"]["qId"]
            dh = self.call(self.open(), "GetDimension", [did])["qReturn"]["qHandle"]
            p = self.call(dh, "GetProperties", [])["qProp"]
            meta = p.get("qMetaDef", {})
            dim = p.get("qDim", {})
            rows.append({"id": did, "name": meta.get("title", ""),
                         "grouping": dim.get("qGrouping", ""),
                         "fields": "; ".join(dim.get("qFieldDefs", []) or []),
                         "field_list": list(dim.get("qFieldDefs", []) or []),
                         "field_labels": list(dim.get("qFieldLabels", []) or []),
                         "label_expression": dim.get("qLabelExpression", ""),
                         "description": meta.get("description", ""),
                         "tags": list(meta.get("tags", []) or [])})
        return rows

    def variables(self):
        rows = []
        for it in self._list("variable", "qVariableList", "qVariableListDef",
                             {"qShowReserved": True, "qShowConfig": True, "qData": {"tags": "/tags"}}):
            script_made = bool(it.get("qIsScriptCreated", False))
            # A script-created variable holds a value computed from data
            # (LET vMax = Peek(...)); the value is withheld, the name kept.
            rows.append({"id": it.get("qInfo", {}).get("qId", ""), "name": it.get("qName", ""),
                         "definition": ("<withheld: script-created value>" if script_made
                                        else it.get("qDefinition", "")),
                         "value_withheld": script_made,
                         "description": it.get("qDescription", ""),
                         "is_script_created": script_made,
                         "is_reserved": bool(it.get("qIsReserved", False)),
                         "tags": list((it.get("qData", {}) or {}).get("tags", []) or [])})
        return rows

    def model_fields(self):
        rows = []
        for it in self._list("", "qFieldList", "qFieldListDef",
                             {"qShowSystem": True, "qShowHidden": True, "qShowSemantic": False,
                              "qShowSrcTables": True, "qShowImplicit": False,
                              "qShowDerivedFields": False}):
            tags = it.get("qTags", []) or []
            src = it.get("qSrcTables", []) or []
            name = it.get("qName", "")
            rows.append({"name": name, "src_tables": list(src),
                         "is_system": bool(it.get("qIsSystem", False)),
                         "is_hidden": bool(it.get("qIsHidden", False)),
                         "is_key": ("$key" in tags) or (len(src) > 1) or name.startswith("$Syn"),
                         "tags": list(tags)})
        return rows

    def tables(self):
        """Data model tables with row counts (GetTablesAndKeys)."""
        res = self.call(self.open(), "GetTablesAndKeys",
                        [{"qcx": 0, "qcy": 0}, {"qcx": 0, "qcy": 0}, 0, True, False])
        out = []
        for t in res.get("qtr", []) or []:
            out.append({"name": t.get("qName", ""), "rows": t.get("qNoOfRows"),
                        "fields": len(t.get("qFields", []) or []),
                        "is_synthetic": bool(t.get("qIsSynthetic", False))})
        return out

    def sheets(self):
        d = {"qInfo": {"qType": "SheetList"},
             "qAppObjectListDef": {"qType": "sheet",
                                   "qData": {"title": "/qMetaDef/title", "cells": "/cells"}}}
        h = self.call(self.open(), "CreateSessionObject", [d])["qReturn"]["qHandle"]
        try:
            return self.call(h, "GetLayout", [])["qLayout"]["qAppObjectList"]["qItems"]
        finally:
            try:
                self.call(self.open(), "DestroySessionObject", [h])
            except Exception:
                pass

    def objects(self):
        """Every visualisation on every sheet, with every expression it
        holds and every master item it references."""
        rows = []
        for s in self.sheets():
            sheet_title = s.get("qData", {}).get("title", s["qInfo"]["qId"])
            for cell in s.get("qData", {}).get("cells", []) or []:
                obj_id = cell.get("name")
                if not obj_id:
                    continue
                try:
                    oh = self.call(self.open(), "GetObject", [obj_id])["qReturn"]["qHandle"]
                    tree = self.call(oh, "GetFullPropertyTree", [])["qPropEntry"]
                except EngineError:
                    continue
                stack = [tree]
                while stack:
                    entry = stack.pop()
                    walk_object(entry.get("qProperty", {}), sheet_title, rows)
                    stack.extend(entry.get("qChildren", []) or [])
        return rows

    def lineage(self):
        return self.call(self.open(), "GetLineage", []).get("qLineage", []) or []

    def sheet_list(self):
        """Sheets with id, title, description, rank and object count."""
        out = []
        for s in self.sheets():
            qd = s.get("qData", {}) or {}
            meta = s.get("qMeta", {}) or {}
            out.append({"id": s["qInfo"]["qId"], "title": qd.get("title") or meta.get("title") or "",
                        "description": (meta.get("description") or "")[:500],
                        "published": meta.get("published"), "approved": meta.get("approved"),
                        "rank": qd.get("rank"), "objects": len(qd.get("cells") or [])})
        return out

    def sheet_objects(self, sheet_id: str):
        """Every object on one sheet: type, title, dimensions and measures as
        expressions or master item ids, and every other expression it holds.
        Definitions only; GetLayout is never called on a chart."""
        oh = self.call(self.open(), "GetObject", [sheet_id])["qReturn"]["qHandle"]
        tree = self.call(oh, "GetFullPropertyTree", [])["qPropEntry"]
        sheet_prop = tree.get("qProperty", {}) or {}
        title = (sheet_prop.get("qMetaDef") or {}).get("title", "")
        rows = []
        for child in tree.get("qChildren", []) or []:
            stack = [child]
            while stack:
                entry = stack.pop()
                prop = entry.get("qProperty", {}) or {}
                info = prop.get("qInfo", {}) or {}
                hc = prop.get("qHyperCubeDef") or {}
                dims = [{"library_id": d.get("qLibraryId", ""), "fields": (d.get("qDef") or {}).get("qFieldDefs") or [],
                         "label": (d.get("qDef") or {}).get("qFieldLabels") or []} for d in hc.get("qDimensions", []) or []]
                meas = [{"library_id": m.get("qLibraryId", ""), "expression": (m.get("qDef") or {}).get("qDef", ""),
                         "label": (m.get("qDef") or {}).get("qLabel", "")} for m in hc.get("qMeasures", []) or []]
                lo = prop.get("qListObjectDef") or {}
                if lo:
                    dims.append({"library_id": lo.get("qLibraryId", ""), "fields": (lo.get("qDef") or {}).get("qFieldDefs") or [],
                                 "label": []})
                t = prop.get("title", "")
                if isinstance(t, dict):
                    t = (t.get("qStringExpression") or {}).get("qExpr", "")
                exprs, libs = [], set()
                harvest_expressions(prop, exprs, libs)
                rows.append({"id": info.get("qId", ""), "type": prop.get("visualization") or info.get("qType", ""),
                             "title": t if isinstance(t, str) else "", "subtitle": prop.get("subtitle", "") if isinstance(prop.get("subtitle"), str) else "",
                             "dimensions": dims, "measures": meas,
                             "other_expressions": sorted({e for e in exprs if e})[:50],
                             "children": len(entry.get("qChildren", []) or [])})
                stack.extend(entry.get("qChildren", []) or [])
        return {"sheet_id": sheet_id, "title": title, "objects": rows}

    def data_model(self):
        """Tables with their fields and keys (GetTablesAndKeys): structure only."""
        res = self.call(self.open(), "GetTablesAndKeys",
                        [{"qcx": 0, "qcy": 0}, {"qcx": 0, "qcy": 0}, 0, True, False])
        tables = []
        for t in res.get("qtr", []) or []:
            tables.append({"name": t.get("qName", ""), "rows": t.get("qNoOfRows"), "is_synthetic": bool(t.get("qIsSynthetic", False)),
                           "fields": [{"name": f.get("qName", ""), "is_key": bool(f.get("qKeyType") and f.get("qKeyType") != "NOT_KEY"),
                                       "key_type": f.get("qKeyType") or "", "tags": list(f.get("qTags") or []),
                                       "distinct_values": f.get("qnTotalDistinctValues")}
                                      for f in t.get("qFields", []) or []]})
        keys = [{"fields": list(k.get("qKeyFields") or []), "tables": list(k.get("qTables") or [])}
                for k in res.get("qk", []) or []]
        return {"tables": tables, "keys": keys}

    # ---------------------------------------------------------------- writes
    def create_sheet(self, title: str, description: str = "") -> str:
        props = {"qInfo": {"qType": "sheet"}, "qMetaDef": {"title": title, "description": description},
                 "rank": -1, "thumbnail": {"qStaticContentUrlDef": {}}, "columns": 24, "rows": 12, "cells": []}
        h = self.call(self.open(), "CreateObject", [props])
        return (h.get("qInfo") or {}).get("qId") or ""

    def add_chart(self, sheet_id: str, viz_type: str, title: str, dimensions: list, measures: list,
                  col: int = 0, row: int = 0, colspan: int = 12, rowspan: int = 6) -> str:
        """Create a chart as a child of the sheet and place it in the grid.
        dimensions: [{"field": ...} or {"library_id": ...}]; measures:
        [{"expression": ..., "label": ...} or {"library_id": ...}]."""
        qdims, qmeas = [], []
        for d in dimensions or []:
            if d.get("library_id"):
                qdims.append({"qLibraryId": d["library_id"]})
            elif d.get("field"):
                qdims.append({"qDef": {"qFieldDefs": [d["field"]], "qFieldLabels": [d.get("label", "")]}})
        for m in measures or []:
            if m.get("library_id"):
                qmeas.append({"qLibraryId": m["library_id"]})
            elif m.get("expression"):
                qmeas.append({"qDef": {"qDef": m["expression"], "qLabel": m.get("label", "")}})
        props = {"qInfo": {"qType": viz_type}, "visualization": viz_type, "title": title, "showTitles": True,
                 "qHyperCubeDef": {"qDimensions": qdims, "qMeasures": qmeas, "qInitialDataFetch": [],
                                   "qSuppressZero": False, "qSuppressMissing": True}}
        sh = self.call(self.open(), "GetObject", [sheet_id])["qReturn"]["qHandle"]
        child = self.call(sh, "CreateChild", [props])
        child_id = (child.get("qInfo") or {}).get("qId") or ""
        sheet_prop = self.call(sh, "GetProperties", [])["qProp"]
        cells = list(sheet_prop.get("cells") or [])
        cells.append({"name": child_id, "type": viz_type, "col": col, "row": row, "colspan": colspan, "rowspan": rowspan,
                      "bounds": {"y": row / 12 * 100, "x": col / 24 * 100, "width": colspan / 24 * 100, "height": rowspan / 12 * 100}})
        sheet_prop["cells"] = cells
        self.call(sh, "SetProperties", [sheet_prop])
        return child_id

    def do_save(self):
        self.call(self.open(), "DoSave", [])

    def get_script_raw(self) -> str:
        """The script as stored, for a write-back only. Never returned to a
        tool result; get_script is what results see."""
        return self.call(self.open(), "GetScript", [])["qScript"] or ""

    def set_script_inserted(self, old: str, new: str, inserted_lines: list) -> int:
        """The only way Ariadne sends SetScript: `new` must be `old` with
        exactly `inserted_lines` inserted. Verified here, after the caller
        verified it too, because this is where the socket is."""
        pos = verify_insert(old, new, inserted_lines)
        self.call(self.open(), "SetScript", [new])
        return pos

    def bundle(self) -> dict:
        """Everything compare_app_versions looks at, in one read."""
        return {"script": self.get_script() or "", "variables": self.variables(),
                "measures": self.measures(), "dimensions": self.dimensions(), "objects": self.objects()}


# ------------------------------------------------------------ object walk
def harvest_expressions(node, exprs, libs, wanted=False, depth=0):
    """Every expression string and master-item id anywhere in a property
    tree: colour expressions, calc conditions, sort expressions, subtitles,
    extension properties. A field referenced only there used to read as
    unused - the one error this must not make."""
    if depth > 24:
        return
    if isinstance(node, str):
        if wanted and node.strip():
            exprs.append(node)
        return
    if isinstance(node, list):
        for v in node:
            harvest_expressions(v, exprs, libs, wanted, depth + 1)
        return
    if isinstance(node, dict):
        for k, v in node.items():
            kl = str(k).lower()
            if kl == "qlibraryid":
                if isinstance(v, str) and v:
                    libs.add(v)
                continue
            harvest_expressions(v, exprs, libs,
                                kl in _EXPR_KEYS or bool(_EXPR_KEY_HINT.search(kl)), depth + 1)


def walk_object(prop: dict, sheet_title: str, rows: list):
    info = prop.get("qInfo", {})
    viz = prop.get("visualization") or info.get("qType", "")
    measure_libs, dim_libs, exprs = [], [], []
    hc = prop.get("qHyperCubeDef") or {}
    for d in hc.get("qDimensions", []) or []:
        lib = d.get("qLibraryId", "")
        if lib:
            dim_libs.append(lib)
        else:
            exprs.extend(d.get("qDef", {}).get("qFieldDefs", []) or [])
    for m in hc.get("qMeasures", []) or []:
        lib = m.get("qLibraryId", "")
        if lib:
            measure_libs.append(lib)
        else:
            expr = m.get("qDef", {}).get("qDef", "")
            if expr:
                exprs.append(expr)
    lo = prop.get("qListObjectDef") or {}
    if lo:
        lib = lo.get("qLibraryId", "")
        if lib:
            dim_libs.append(lib)
        else:
            exprs.extend(lo.get("qDef", {}).get("qFieldDefs", []) or [])
    title = prop.get("title", "")
    if isinstance(title, dict):
        title = title.get("qStringExpression", {}).get("qExpr", "")
    extra, libs = [], set()
    harvest_expressions(prop, extra, libs)
    exprs.extend(extra)
    known = set(measure_libs) | set(dim_libs)
    dim_libs.extend(sorted(libs - known))
    if viz:
        rows.append({"sheet": sheet_title, "id": info.get("qId", ""), "type": viz,
                     "title": title if isinstance(title, str) else "",
                     "measure_libs": measure_libs, "dim_libs": dim_libs,
                     "expressions": sorted({e for e in exprs if e})})
