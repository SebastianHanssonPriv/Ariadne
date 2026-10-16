"""Ownership continuity: content whose owner has left, and content that
rests on too few people.

A lookup that FAILS says nothing about the owner: it is UNVERIFIED, listed on
its own, never read as "fine" and never as "gone". A 404 is evidence (no such
user, or one this key may not see); a 500 is not.
"""
from __future__ import annotations

import datetime

RISK, REVIEW, ACTIVE, UNVERIFIED = "risk", "review", "active", "unverified"
TOP_OWNERS = 3


def days_since(iso: str):
    if not iso:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return max(0, (datetime.datetime.now(datetime.timezone.utc) - dt).days)


def classify(info: dict):
    found, status = info.get("found"), (info.get("status") or "").lower()
    if found is False:
        return RISK, "Not found (deleted, or not visible to this key)"
    if found is None:
        return UNVERIFIED, "Unverified (the lookup failed)"
    if status in ("disabled", "deleted"):
        return RISK, status.capitalize()
    if status == "active":
        return ACTIVE, "Active"
    if not status:
        return UNVERIFIED, "Unverified (no status reported)"
    return REVIEW, f"Review: status '{status}'"


def priority(app: dict, space_type: str):
    """(score, why): how much it matters that nobody answers for this app.
    An input the tenant did not report scores 0 and says so."""
    score, why = 0, []
    pub = app.get("published")
    if pub is None:
        why.append("publish state not reported (0)")
    elif pub:
        score += 2
        why.append("published (+2)")
    else:
        why.append("not published (0)")
    st = (space_type or "").lower()
    if st == "managed":
        score += 2
        why.append("managed space (+2)")
    elif st == "shared":
        score += 1
        why.append("shared space (+1)")
    elif st in ("", "unknown"):
        why.append("space type not reported (0)")
    else:
        why.append(f"{st} space (0)")
    age = days_since(app.get("reloaded"))
    if age is None:
        why.append("no reload time reported (0)")
    elif age <= 30:
        score += 2
        why.append(f"reloaded {age} d ago (+2)")
    elif age <= 90:
        score += 1
        why.append(f"reloaded {age} d ago (+1)")
    else:
        why.append(f"last reload {age} d ago (0)")
    return score, "; ".join(why)


def scan_ownership(apps, spaces, looked: dict):
    """apps: Rest.list_apps(); spaces: Rest.list_spaces(); looked: {uid: user_info}."""
    uids = sorted({a.get("owner_id") for a in apps if a.get("owner_id")}
                  | {s.get("owner_id") for s in spaces.values() if s.get("owner_id")})
    owners = {}
    for uid in uids:
        info = looked.get(uid) or {"name": "", "status": "", "found": None}
        kind, label = classify(info)
        owners[uid] = {"id": uid, "name": info.get("name") or "", "status": info.get("status") or "",
                       "kind": kind, "label": label, "apps": 0, "published": 0}
    app_rows, no_owner = [], 0
    for a in apps:
        uid = a.get("owner_id") or ""
        sp = spaces.get(a.get("space_id") or "") or {}
        space_type = sp.get("type") or ("unknown" if a.get("space_id") else "personal")
        if not uid:
            no_owner += 1
            continue
        o = owners[uid]
        o["apps"] += 1
        if a.get("published"):
            o["published"] += 1
        if o["kind"] in (RISK, REVIEW, UNVERIFIED):
            score, why = priority(a, space_type)
            app_rows.append({"app": a["name"], "app_id": a["guid"], "space": sp.get("name") or
                             ("Personal" if not a.get("space_id") else a.get("space_id")),
                             "space_type": space_type, "owner": o["name"] or uid,
                             "owner_state": o["kind"], "owner_label": o["label"],
                             "priority": score, "why": why})
    app_rows.sort(key=lambda r: (0 if r["owner_state"] == RISK else 1 if r["owner_state"] == REVIEW else 2,
                                 -r["priority"], r["app"].lower()))
    space_rows = []
    for sid, s in spaces.items():
        uid = s.get("owner_id") or ""
        if not uid:
            continue
        o = owners[uid]
        if o["kind"] in (RISK, REVIEW, UNVERIFIED):
            space_rows.append({"space": s.get("name") or sid, "space_id": sid, "type": s.get("type") or "",
                               "owner": o["name"] or uid, "owner_state": o["kind"],
                               "owner_label": o["label"]})
    published_total = sum(o["published"] for o in owners.values())
    top = sorted(owners.values(), key=lambda o: -o["published"])[:TOP_OWNERS]
    top_share = (sum(o["published"] for o in top) / published_total) if published_total else None
    counts = {k: sum(1 for o in owners.values() if o["kind"] == k)
              for k in (RISK, REVIEW, UNVERIFIED, ACTIVE)}
    return {"owners": counts, "apps_at_risk": app_rows, "spaces_at_risk": space_rows,
            "apps_without_owner_id": no_owner,
            "concentration": {"top_owners": [{"name": o["name"] or o["id"], "published_apps": o["published"]}
                                             for o in top],
                              "share_of_published": (round(top_share, 3) if top_share is not None else None),
                              "published_total": published_total}}
