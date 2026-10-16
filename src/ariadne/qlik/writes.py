"""Master-item writes. Exact title, duplicates in full, near miss refused,
dry run exact. Ported from the source application's apply path and kept to
create and update: there is no delete mode.
"""
from __future__ import annotations

from .policy import PolicyError

MODES = ("upsert", "create", "update")


def build_measure(row: dict):
    meas = {"qDef": row.get("expression", "") or ""}
    if row.get("label"):
        meas["qLabel"] = row["label"]
    if row.get("label_expression"):
        meas["qLabelExpression"] = row["label_expression"]
    num = {}
    if row.get("format_type"):
        num["qType"] = row["format_type"]
    if row.get("format_pattern"):
        num["qFmt"] = row["format_pattern"]
    if num:
        meas["qNumFormat"] = num
    meta = {"title": (row.get("name") or "").strip()}
    if row.get("description"):
        meta["description"] = row["description"]
    if row.get("tags"):
        meta["tags"] = _list(row["tags"], ",")
    return meas, meta


def build_dimension(row: dict):
    fields = row.get("fields") or row.get("field_list") or []
    dim = {"qGrouping": (row.get("grouping") or "N"), "qFieldDefs": _list(fields, ";")}
    labels = _list(row.get("field_labels") or [], ";")
    if labels:
        dim["qFieldLabels"] = labels
    if row.get("label_expression"):
        dim["qLabelExpression"] = row["label_expression"]
    meta = {"title": (row.get("name") or "").strip()}
    if row.get("description"):
        meta["description"] = row["description"]
    if row.get("tags"):
        meta["tags"] = _list(row["tags"], ",")
    return dim, meta


def _list(v, sep):
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [s.strip() for s in str(v or "").split(sep) if s.strip()]


def apply_master(session, kind: str, rows, mode: str, dry_run: bool):
    """session: an EngineSession with open() and call(). Returns
    {counts, actions}; actions is the exact list of what was (or would be)
    done, one entry per item id."""
    if mode not in MODES:
        raise PolicyError(f"mode {mode!r} is not available; Ariadne creates and updates, never deletes")
    if kind not in ("measure", "dimension"):
        raise PolicyError(f"kind must be 'measure' or 'dimension', not {kind!r}")
    is_meas = kind == "measure"
    list_type, list_key, layout_key = (("MeasureList", "qMeasureListDef", "qMeasureList") if is_meas
                                       else ("DimensionList", "qDimensionListDef", "qDimensionList"))
    create_fn, get_fn, prop_key = (("CreateMeasure", "GetMeasure", "qMeasure") if is_meas
                                   else ("CreateDimension", "GetDimension", "qDim"))
    app_h = session.open()
    obj_h = session.call(app_h, "CreateSessionObject",
                         [{"qInfo": {"qType": list_type}, list_key: {"qType": kind}}])["qReturn"]["qHandle"]
    items = session.call(obj_h, "GetLayout", [])["qLayout"][layout_key]["qItems"]
    try:
        session.call(app_h, "DestroySessionObject", [obj_h])
    except Exception:
        pass
    by_title, by_loose = {}, {}
    for it in items:
        t = (it.get("qMeta") or {}).get("title", "")
        i = (it.get("qInfo") or {}).get("qId", "")
        if i:
            by_title.setdefault(t, []).append(i)
    for t in by_title:
        by_loose.setdefault(t.strip().lower(), []).append(t)

    counts = {"created": 0, "updated": 0, "skipped": 0, "ambiguous": 0}
    actions = []
    for row in rows or []:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        ids = by_title.get(name) or []
        if not ids:
            close = [t for t in by_loose.get(name.strip().lower(), []) if t != name]
            if close:
                counts["ambiguous"] += 1
                actions.append({"action": "AMBIGUOUS", "kind": kind, "name": name,
                                "detail": f"the app has {', '.join(repr(c) for c in close)}, differing only "
                                          "in case or spacing; nothing written. Match the title exactly."})
                continue
        body, meta = build_measure(row) if is_meas else build_dimension(row)
        if ids:
            if mode == "create":
                counts["skipped"] += 1
                actions.append({"action": "SKIP", "kind": kind, "name": name, "detail": "exists; mode=create"})
                continue
            for i in ids:
                actions.append({"action": "UPDATE", "kind": kind, "name": name, "id": i,
                                "detail": (f"{len(ids)} items share this title; all updated" if len(ids) > 1 else "")})
                if not dry_run:
                    h = session.call(app_h, get_fn, [i])["qReturn"]["qHandle"]
                    prop = session.call(h, "GetProperties", [])["qProp"]
                    prop.setdefault(prop_key, {}).update(body)       # shallow merge: unmentioned keeps its value
                    prop.setdefault("qMetaDef", {}).update(meta)
                    session.call(h, "SetProperties", [prop])
                counts["updated"] += 1
        else:
            if mode == "update":
                counts["skipped"] += 1
                actions.append({"action": "SKIP", "kind": kind, "name": name, "detail": "missing; mode=update"})
                continue
            actions.append({"action": "CREATE", "kind": kind, "name": name})
            if not dry_run:
                session.call(app_h, create_fn, [{"qInfo": {"qType": kind}, prop_key: body, "qMetaDef": meta}])
            counts["created"] += 1
            by_title.setdefault(name, []).append("(created this run)")
            by_loose.setdefault(name.strip().lower(), []).append(name)
    return {"counts": counts, "actions": actions}
