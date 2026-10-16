"""Snapshot before write: a copy of the published state, without data, in a
designated archive space, once per publish.

Routing is by rule (space name pattern -> archive space), refused when the
rule gives no single answer. A snapshot is identified by a marker at the
start of its description, so the next write after the same publish finds
it and takes none.
"""
from __future__ import annotations

import datetime
import fnmatch
import re

from .policy import PolicyError

MARKER = "[ariadne snapshot]"
_MARK = re.compile(r"\[ariadne snapshot\]\s+source=(\S+)\s+copy_of=(\S+)\s+published=(\S+)\s+taken=(\S+)\s+data=(\S+)")


def marker(source_id: str, copy_of: str, published: str, taken: str, with_data: bool) -> str:
    return (f"{MARKER} source={source_id} copy_of={copy_of} published={published or '-'} "
            f"taken={taken} data={'yes' if with_data else 'no'}")


def parse_marker(description: str):
    m = _MARK.search(description or "")
    if not m:
        return None
    return {"source": m.group(1), "copy_of": m.group(2), "published": m.group(3),
            "taken": m.group(4), "with_data": m.group(5) == "yes"}


def archive_spaces(settings) -> set:
    return {s for s in [settings.archive_space_id, *settings.archive_map.values()] if s}


def route(space_id: str, space_name: str, settings, explicit: str = ""):
    """(archive_space_id, reason). Raises PolicyError when no single answer."""
    valid = archive_spaces(settings)
    if explicit:
        if explicit not in valid:
            raise PolicyError(f"archive space {explicit!r} is not one of the configured archive spaces "
                              f"{sorted(valid)}; nothing written")
        return explicit, "chosen by caller"
    hits = {}
    for pat, aid in settings.archive_map.items():
        if pat == space_id or fnmatch.fnmatch((space_name or "").lower(), pat.lower()):
            hits.setdefault(aid, []).append(pat)
    if len(hits) > 1:
        raise PolicyError(f"space {space_name!r} matches several archive rules {sorted(hits.values())}; "
                          "pass archive_space_id to choose")
    if hits:
        aid, pats = next(iter(hits.items()))
        return aid, f"rule {pats[0]!r}"
    if settings.archive_space_id:
        return settings.archive_space_id, "default archive space"
    raise PolicyError("no archive space configured (ARIADNE_ARCHIVE_SPACE_ID); content writes are refused")


def wants_data(app: dict, settings) -> bool:
    for pat in settings.snapshot_with_data:
        if pat == app.get("guid") or fnmatch.fnmatch((app.get("name") or "").lower(), pat.lower()):
            return True
    return False


def subjects(app: dict, apps) -> list:
    """The published states of `app`: its published copies (origin_app_id ==
    app), and the app itself when it is a published copy."""
    out = [a for a in apps if a.get("origin_app_id") == app["guid"] and a.get("publish_time")]
    if app.get("publish_time") and app["guid"] not in {a["guid"] for a in out}:
        out.append(app)
    return out


def plan(app: dict, apps, spaces: dict, settings, explicit_archive: str = "", today: str = ""):
    today = today or datetime.date.today().isoformat()
    subs = subjects(app, apps)
    if not subs:
        return {"applies": False, "reason": "no published copy of this app is visible to this key; "
                                            "nothing to snapshot", "items": []}
    items, blocked = [], []
    for s in subs:
        sp = spaces.get(s.get("space_id") or "") or {}
        try:
            archive, why = route(s.get("space_id") or "", sp.get("name") or "", settings, explicit_archive)
        except PolicyError as e:
            blocked.append(str(e))
            continue
        existing = [a for a in apps if a.get("space_id") == archive
                    and (m := parse_marker(a.get("description") or ""))
                    and m["source"] == app["guid"] and m["copy_of"] == s["guid"]
                    and m["published"] == (s.get("publish_time") or "-")]
        items.append({
            "copy_of": s["guid"], "copy_name": s.get("name", ""),
            "from_space": sp.get("name") or s.get("space_id") or "Personal",
            "published": s.get("publish_time") or "", "archive_space_id": archive, "routing": why,
            "with_data": wants_data(app, settings),
            "name": f"{s.get('name', '')} {today} (published {(s.get('publish_time') or '')[:10]})",
            "existing_snapshot_id": existing[0]["guid"] if existing else "",
            "needed": not existing,
        })
    return {"applies": True, "items": items, "blocked": blocked,
            "reason": ("snapshot needed for %d of %d published state(s)" % (sum(i["needed"] for i in items), len(items)))}


def execute(rest, app: dict, item: dict, original_description: str = "") -> dict:
    """Export (NoData unless with_data), import into the archive space,
    verify the space, stamp the marker. Raises PolicyError if the copy did
    not land in the archive; the copy's id is in the message, never deleted."""
    taken = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    qvf = rest.export_app(item["copy_of"], no_data=not item["with_data"])
    new = rest.import_app(qvf, item["name"], item["archive_space_id"])
    new_id = new.get("id") or ""
    if not new_id:
        raise PolicyError("import returned no app id; write refused")
    if (new.get("spaceId") or "") != item["archive_space_id"]:
        rest.move_app(new_id, item["archive_space_id"])
        check = (rest.app(new_id).get("attributes") or {})
        if (check.get("spaceId") or "") != item["archive_space_id"]:
            raise PolicyError(f"snapshot {new_id} could not be placed in archive space "
                              f"{item['archive_space_id']} (it is in {check.get('spaceId') or 'Personal'}); "
                              "write refused. Move or remove it by hand.")
    desc = marker(app["guid"], item["copy_of"], item["published"], taken, item["with_data"])
    if original_description:
        desc += "\n" + original_description
    rest.set_app_description(new_id, desc)
    return {"snapshot_id": new_id, "name": item["name"], "archive_space_id": item["archive_space_id"],
            "with_data": item["with_data"], "taken": taken, "copy_of": item["copy_of"]}
