"""Qlik expressions: what they reference, and when two are the same.

`referenced_names` decides whether a field counts as used, so it must err
toward "used" (a stale comment must not hide a live field, and a bare name
that is also a function name still counts). `tight` decides whether two master
items are the same calculation, so it must make neither error: whitespace
outside literals is nothing, whitespace inside 'United Kingdom' is real.
"""
from __future__ import annotations

import re

_PLAIN_IDENT = re.compile(r"^[^\W\d]\w*$")
DOLLAR_RE = re.compile(r"\$\(([^)]*)\)")
_QUOTED_NAME = re.compile(r'\[((?:[^\]]|\]\])+)\]|"((?:[^"]|"")+)"')


def _quoted_end(src: str, i: int, close: str):
    j = i + 1
    while True:
        k = src.find(close, j)
        if k == -1:
            return None
        if src[k + 1:k + 2] == close:
            j = k + 2
            continue
        return k + 1


def tight(s: str) -> str:
    """Comparison key for a Qlik expression: same calculation, same key.
    Quote-aware: comments dropped, whitespace and case folded outside
    literals, literals kept as written apart from case, brackets dropped only
    round a plain identifier. 'X' (string) and "X" (field) stay different;
    "Net Sales" and [Net Sales] are one field reference."""
    src = s or ""
    out = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if ch == "'":
            j = _quoted_end(src, i, "'")
            if j is None:
                out.append(src[i:].lower())
                break
            out.append(src[i:j].lower())
            i = j
            continue
        if ch in ('"', "["):
            close = '"' if ch == '"' else "]"
            j = _quoted_end(src, i, close)
            if j is not None:
                inner = src[i + 1:j - 1].replace(close * 2, close)
                out.append(inner.lower() if _PLAIN_IDENT.match(inner)
                           else "[" + inner.lower() + "]")
                i = j
                continue
            if ch == '"':
                out.append(src[i:].lower())
                break
        if ch.isspace():
            i += 1
            continue
        out.append(ch.lower())
        i += 1
    return "".join(out)


def expand_vars(expr: str, varmap: dict, max_iter: int = 6) -> str:
    """Substitute $(var) from a name->definition map. $(=...) and $(f(x)) are
    left as they are: they are evaluated at run time and cannot be known."""
    out = expr or ""
    for _ in range(max_iter):
        changed = False
        for name, defn in varmap.items():
            if not name:
                continue
            token = "$(" + name + ")"
            if token in out:
                out = out.replace(token, defn or "")
                changed = True
        if not changed:
            break
    return out


def split_expression(text: str):
    """(code, literals): the part that executes, and the contents of its
    string literals. Comments belong to neither."""
    src = text or ""
    code, lits = [], []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if ch == "'":
            j = _quoted_end(src, i, "'")
            if j is None:
                lits.append(src[i + 1:])
                break
            lits.append(src[i + 1:j - 1].replace("''", "'"))
            code.append(" ")
            i = j
            continue
        if ch in ('"', "["):
            j = _quoted_end(src, i, '"' if ch == '"' else "]")
            if j is not None:
                code.append(src[i:j])
                i = j
                continue
        code.append(ch)
        i += 1
    return "".join(code), " ".join(lits)


def referenced_names(text: str) -> set:
    """Every name an expression references, lowercased. Comments never count;
    a string literal counts only when the expression contains `$(`, where a
    literal can become code. Deliberately loose: every bare identifier counts,
    so the report under-reports unused fields rather than over-reports."""
    names = set()
    if not text:
        return names
    code, lits = split_expression(text)
    harvest = code if "$(" not in text else code + " " + lits
    for m in _QUOTED_NAME.finditer(harvest):
        if m.group(1) is not None:
            names.add(m.group(1).replace("]]", "]").strip().lower())
        else:
            names.add(m.group(2).replace('""', '"').strip().lower())
    for t in re.findall(r"[^\W\d][\w.]*", harvest):
        names.add(t.lower())
    return names


# ------------------------------------------------------- cross-app consistency
def _name_conflicts(items):
    by_name = {}
    for it in items:
        nk = (it["name"] or "").strip().lower()
        by_name.setdefault(nk, []).append(it)
    out = []
    for group in by_name.values():
        variants = {}
        for it in group:
            v = variants.setdefault(it["calc_key"], {"expr": it["calc"], "apps": []})
            v["apps"].append(it["app"])
        if len(variants) > 1:
            out.append({"name": group[0]["name"], "variant_count": len(variants),
                        "variants": [{"expression": v["expr"], "apps": sorted(set(v["apps"]))}
                                     for v in variants.values()]})
    out.sort(key=lambda x: (-x["variant_count"], (x["name"] or "").lower()))
    return out


def _redundancy(items):
    by_calc = {}
    for it in items:
        if not it["calc_key"]:
            continue
        by_calc.setdefault(it["calc_key"], []).append(it)
    out = []
    for group in by_calc.values():
        names = sorted({(it["name"] or "") for it in group})
        apps = sorted({it["app"] for it in group})
        if len(names) > 1:
            out.append({"expression": group[0]["calc"], "names": names,
                        "apps": apps, "occurrences": len(group)})
    out.sort(key=lambda x: (-len(x["names"]), -x["occurrences"]))
    return out


def analyze_consistency(measures, dims):
    """measures: [{name, expression, app[, expr_expanded]}];
    dims: [{name, definition, app[, def_expanded]}]."""
    m_items = [{"name": m["name"], "calc": m.get("expr_expanded") or m["expression"],
                "calc_key": tight(m.get("expr_expanded") or m["expression"]),
                "app": m["app"]} for m in measures]
    d_items = [{"name": d["name"], "calc": d.get("def_expanded") or d["definition"],
                "calc_key": tight(d.get("def_expanded") or d["definition"]),
                "app": d["app"]} for d in dims]
    return {
        "measure_name_conflicts": _name_conflicts(m_items),
        "measure_redundancy": _redundancy(m_items),
        "dimension_name_conflicts": _name_conflicts(d_items),
        "dimension_redundancy": _redundancy(d_items),
    }


# ------------------------------------------------------------ usage in an app
def usage_corpus(measures, dimensions, variables, objects):
    texts = []
    for me in measures:
        texts.append(me.get("expression", ""))
        texts.append(me.get("label_expression", ""))
    for di in dimensions:
        texts.append(di.get("fields", ""))
        texts.append(di.get("label_expression", ""))
    for v in variables:
        texts.append(v.get("definition", ""))
    for o in objects:
        texts.extend(o.get("expressions", []) or [])
    return [t for t in texts if t]


def find_dynamic_expressions(measures, dimensions, variables, objects):
    out = []

    def scan(location, text):
        if not text:
            return
        for m in DOLLAR_RE.finditer(text):
            inner = m.group(1).strip()
            kind = "$(=...) evaluated at run time - HIDES USAGE" if inner.startswith("=") \
                else "$(var) reference"
            out.append({"location": location, "type": kind, "expression": m.group(0)})

    for me in measures:
        scan(f"Master measure: {me.get('name', '')}", me.get("expression", ""))
        scan(f"Master measure label: {me.get('name', '')}", me.get("label_expression", ""))
    for di in dimensions:
        scan(f"Master dimension: {di.get('name', '')}", di.get("fields", ""))
        scan(f"Master dimension label: {di.get('name', '')}", di.get("label_expression", ""))
    for v in variables:
        scan(f"Variable: {v.get('name', '')}", v.get("definition", ""))
    for o in objects:
        loc = f"{o.get('type', 'object')} on sheet '{o.get('sheet', '')}'"
        for e in o.get("expressions", []) or []:
            scan(loc, e)
    return out


def analyze_master_usage(measures, dimensions, objects):
    use = {}
    for o in objects:
        for lib in (o.get("measure_libs", []) or []) + (o.get("dim_libs", []) or []):
            use.setdefault(lib, []).append(o)

    def rows_for(items, kind):
        res = []
        for it in items:
            objs = use.get(it.get("id", ""), [])
            sheets = sorted({o.get("sheet", "") for o in objs})
            res.append({"kind": kind, "id": it.get("id", ""), "name": it.get("name", ""),
                        "definition": it.get("expression", "") if kind == "measure" else it.get("fields", ""),
                        "use_count": len(objs), "sheets": [s for s in sheets if s],
                        "used": bool(objs)})
        return res

    allrows = rows_for(measures, "measure") + rows_for(dimensions, "dimension")
    return {"all": allrows, "unused": [r for r in allrows if not r["used"]]}


def analyze_field_usage(model_fields, corpus):
    refs = set()
    for t in corpus:
        refs |= referenced_names(t)
    rows = []
    for f in model_fields:
        nm = (f["name"] or "").lower()
        used = nm in refs
        if f["is_key"]:
            rec = "Key - keep"
        elif f["is_system"]:
            rec = "System - keep"
        elif f["is_hidden"]:
            rec = "Hidden - keep"
        else:
            rec = "Used" if used else "Unused candidate"
        rows.append({"name": f["name"], "tables": list(f["src_tables"]),
                     "is_key": f["is_key"], "is_system": f["is_system"], "is_hidden": f["is_hidden"],
                     "used": used, "recommendation": rec})
    return {"all": rows, "unused": [r for r in rows if r["recommendation"] == "Unused candidate"]}


def analyze_variable_usage(variables, corpus):
    refs = set()
    for t in corpus:
        refs |= referenced_names(t)
    rows = []
    for v in variables:
        if v.get("is_reserved"):
            continue
        nm = (v.get("name", "") or "").lower()
        rows.append({"name": v.get("name", ""), "definition": v.get("definition", ""),
                     "is_script_created": v.get("is_script_created", False), "used": nm in refs})
    return {"all": rows, "unused": [r for r in rows if not r["used"]]}


def analyze_usage(measures, dimensions, variables, objects, model_fields):
    corpus = usage_corpus(measures, dimensions, variables, objects)
    fields = analyze_field_usage(model_fields, corpus)
    return {
        "dynamic": find_dynamic_expressions(measures, dimensions, variables, objects),
        "master": analyze_master_usage(measures, dimensions, objects),
        "fields": fields,
        "variables": analyze_variable_usage(variables, corpus),
    }
