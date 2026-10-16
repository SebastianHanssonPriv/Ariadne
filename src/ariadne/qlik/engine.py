"""Qlik Engine API (JSON-RPC over WebSocket) for one app at a time.

A session keeps all state on the instance - its own socket, its own request
id - so many can run at once from a thread pool. Opening an app and reading
its load script are different rights (see envelope.SCRIPT_RULE); `get_script`
raises EngineRefused for the permission case so a caller can report it as a
finding rather than an error.
"""
from __future__ import annotations

import json
import re

import websocket

from ..config import normalize_host
from .policy import check_engine

_PERMANENT = ("access denied", "forbidden", "unauthorized", "not authorized",
              "app not found", "resource not found")

# Keys whose string values are Qlik expressions. Matched rather than
# enumerated because extension objects invent their own property names.
_EXPR_KEYS = {"qdef", "qv", "qfielddefs"}
_EXPR_KEY_HINT = re.compile(r"(?i)expr|condition")


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
        self.ws = websocket.create_connection(
            f"wss://{self.host}/app/{self.app_id}",
            header=[f"Authorization: Bearer {self.api_key}"], timeout=self.timeout)
        self.ws.recv()  # OnConnected
        return self

    def call(self, handle, method, params):
        check_engine(method, self.allow_write)
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
        return self.call(self.open(), "GetScript", [])["qScript"]

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
            rows.append({"id": it.get("qInfo", {}).get("qId", ""), "name": it.get("qName", ""),
                         "definition": it.get("qDefinition", ""),
                         "description": it.get("qDescription", ""),
                         "is_script_created": bool(it.get("qIsScriptCreated", False)),
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

    def do_save(self):
        self.call(self.open(), "DoSave", [])

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
