"""What differs between two versions of an app: script, variables, master
items, sheet objects. The input is a bundle read by the engine for each app;
the output names what a rollback would undo."""
from __future__ import annotations

import difflib

from .expressions import tight

MAX_DIFF_LINES = 400


def _by(items, key):
    out = {}
    for it in items:
        k = (it.get(key) or "").strip()
        if k:
            out.setdefault(k, []).append(it)
    return out


def _compare_named(a, b, key, defn):
    """added / removed / changed by name; changed when the definition differs
    on the comparison key (expressions) or verbatim (other text)."""
    A, B = _by(a, key), _by(b, key)
    added = sorted(set(B) - set(A))
    removed = sorted(set(A) - set(B))
    changed = []
    for name in sorted(set(A) & set(B)):
        da = sorted(defn(x) for x in A[name])
        db = sorted(defn(x) for x in B[name])
        if da != db:
            changed.append({"name": name, "from": [x for x in da], "to": [x for x in db]})
    return {"added": added, "removed": removed, "changed": changed,
            "count_a": len(a), "count_b": len(b)}


def compare_bundles(a: dict, b: dict) -> dict:
    """a, b: {script, variables, measures, dimensions, objects}."""
    sa, sb = (a.get("script") or ""), (b.get("script") or "")
    diff = list(difflib.unified_diff(sa.splitlines(), sb.splitlines(), "a", "b", lineterm="", n=2))
    script = {"changed": sa != sb, "lines_a": len(sa.splitlines()), "lines_b": len(sb.splitlines()),
              "diff": diff[:MAX_DIFF_LINES], "diff_truncated": len(diff) > MAX_DIFF_LINES}
    objs_a = {o.get("id"): o for o in a.get("objects") or [] if o.get("id")}
    objs_b = {o.get("id"): o for o in b.get("objects") or [] if o.get("id")}
    obj_changed = []
    for oid in sorted(set(objs_a) & set(objs_b)):
        x, y = objs_a[oid], objs_b[oid]
        if (sorted(x.get("expressions") or []) != sorted(y.get("expressions") or [])
                or x.get("type") != y.get("type") or x.get("title") != y.get("title")
                or sorted(x.get("measure_libs") or []) != sorted(y.get("measure_libs") or [])
                or sorted(x.get("dim_libs") or []) != sorted(y.get("dim_libs") or [])):
            obj_changed.append({"id": oid, "sheet": y.get("sheet") or x.get("sheet"), "type": y.get("type")})
    return {
        "script": script,
        "variables": _compare_named(a.get("variables") or [], b.get("variables") or [], "name",
                                    lambda v: v.get("definition") or ""),
        "measures": _compare_named(a.get("measures") or [], b.get("measures") or [], "name",
                                   lambda m: tight(m.get("expression") or "")),
        "dimensions": _compare_named(a.get("dimensions") or [], b.get("dimensions") or [], "name",
                                     lambda d: tight(d.get("fields") or "")),
        "objects": {"added": sorted(set(objs_b) - set(objs_a)), "removed": sorted(set(objs_a) - set(objs_b)),
                    "changed": obj_changed, "count_a": len(objs_a), "count_b": len(objs_b)},
    }
