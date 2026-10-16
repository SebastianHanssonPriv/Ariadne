"""The developer note: the entry your developers write in the load script
when they have changed something, now written for a change made through
Ariadne after a person approved its text.

The notes live in a block comment that holds only notes, in this shape:

    /*
    - 2026-04-27 - Robert J.:
        - adding rebates tbl and calculation to Net Sales;
        - adding Item Segment to DimItem;
    - 2026-05-10 - Sebastian H.:
        - Added export section for Benchmark;
    */

Oldest first; a new entry is appended at the bottom, before the closing
line. A block is a notes block when it holds at least one entry line, or
when it directly follows a heading that reads "change log", "developer
notes" or "developer log" (with or without hashes). The script that goes
back is verified to be the old script plus the inserted lines and nothing
else; anything else is refused, because SetScript replaces the whole script
and this is the only way Ariadne is allowed to use it.
"""
from __future__ import annotations

import datetime
import re

from .policy import PolicyError

HEADINGS = r"(?:change\s*log|developer\s*notes?|developer\s*log|dev\s*log|dev\s*notes?)"
_HEADING = re.compile(
    r"(?im)^\s*(?:///\$tab\s*|//+\s*|/\*+\s*|REM\s+)?[#*\-=\s]*" + HEADINGS + r"\b[^\n]*$")
_ENTRY = re.compile(r"^\s*-\s*(\d{4}-\d{2}-\d{2})\s*-\s*(.+?):\s*$")
_STAMP = "via Ariadne"


def find_headings(script: str):
    return [(i, l.rstrip("\r")) for i, l in enumerate((script or "").split("\n")) if _HEADING.match(l)]


def find_blocks(script: str):
    """Every /* ... */ block as (open_line, close_line), by line index. The
    closing line is the line that holds the first '*/' after the opener."""
    lines = (script or "").split("\n")
    blocks, open_at = [], None
    for i, line in enumerate(lines):
        if open_at is None:
            if "/*" in line:
                open_at = i
                if "*/" in line.split("/*", 1)[1]:
                    blocks.append((i, i))
                    open_at = None
        elif "*/" in line:
            blocks.append((open_at, i))
            open_at = None
    return blocks


def _is_entry(line: str) -> bool:
    return bool(_ENTRY.match(line))


def find_sections(script: str):
    """[(open_line, close_line, heading_text)] of every notes block."""
    lines = (script or "").split("\n")
    headings = dict(find_headings(script))
    out = []
    for o, c in find_blocks(script):
        body = lines[o + 1:c] if c > o else []
        has_entry = any(_is_entry(l) for l in body)
        # a heading on the opening line, or on a line just above it
        head = ""
        for j in range(o, max(-1, o - 3), -1):
            if j in headings:
                head = headings[j]
                break
        if has_entry or head:
            out.append((o, c, head))
    return out


def author_tag(user_name: str, user_id: str = "") -> str:
    """'Sebastian Hansson' -> 'Sebastian H.'; a single word stays as it is."""
    parts = [p for p in (user_name or "").split() if p]
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1][0]}."
    if parts:
        return parts[0]
    return user_id or "unknown user"


def format_entry(note: str, who: str, tool: str, when: str = "") -> list:
    """The lines of one entry: the header and one tab-indented bullet per
    change. Bullets come from ';' or line breaks in the note."""
    when = when or datetime.date.today().isoformat()
    items = [s.strip().rstrip(";").strip() for s in re.split(r";|\n", note or "") if s.strip()]
    if not items:
        raise PolicyError("a developer note must have text")
    lines = [f"- {when} - {who} ({_STAMP}, {tool}):"]
    for it in items:
        lines.append(f"\t- {it};")
    return lines


def insert(script: str, entry_lines: list, block_line: int | None = None):
    """(new_script, position, heading). Appends the entry at the bottom of
    the notes block, before its closing line. Refuses when no block or
    several blocks are found and none was chosen, or when the closing line
    also holds text (the insert-only guard could not place the entry)."""
    secs = find_sections(script)
    if block_line is not None:
        chosen = next((s for s in secs if s[0] == block_line), None)
        if chosen is None:
            raise PolicyError(f"line {block_line} does not open a developer-notes block; blocks open at "
                              f"{[s[0] for s in secs]}")
    elif not secs:
        raise PolicyError("no developer-notes block found: no /* ... */ block holds an entry like "
                          "'- 2026-05-10 - Name L.:' and none follows a 'change log', 'developer notes' "
                          "or 'developer log' heading. Add the block first; Ariadne does not invent one.")
    elif len(secs) > 1:
        raise PolicyError("several developer-notes blocks found, opening at lines "
                          f"{[s[0] for s in secs]}; pass block_line to choose one")
    else:
        chosen = secs[0]
    lines = (script or "").split("\n")
    o, c, head = chosen
    if o == c or lines[c].strip() != "*/":
        raise PolicyError(f"the notes block closing at line {c} does not end with '*/' on its own line; "
                          "move '*/' to its own line so the entry can be appended without changing a line")
    pos = c
    new = lines[:pos] + entry_lines + lines[pos:]
    new_script = "\n".join(new)
    verify_insert(script, new_script, entry_lines)
    return new_script, pos, head or lines[o].strip()


def verify_insert(old: str, new: str, entry_lines: list) -> int:
    """The line index where `new` is `old` with exactly `entry_lines`
    inserted, or PolicyError. Run before any SetScript."""
    o = (old or "").split("\n")
    n = (new or "").split("\n")
    k = len(entry_lines)
    if len(n) != len(o) + k or k == 0:
        raise PolicyError("script change is not an insertion of the note; nothing sent")
    for pos in range(len(o) + 1):
        if n[:pos] == o[:pos] and n[pos:pos + k] == entry_lines and n[pos + k:] == o[pos:]:
            return pos
    raise PolicyError("script change is not an insertion of the note; nothing sent")


def parse_entries(body_lines) -> list:
    """[{date, author, notes: [...], via_ariadne}] from the lines of a block."""
    out, cur = [], None
    for raw in body_lines:
        line = raw.rstrip("\r")
        m = _ENTRY.match(line)
        if m:
            cur = {"date": m.group(1), "author": m.group(2).strip(), "notes": [],
                   "via_ariadne": _STAMP in m.group(2)}
            out.append(cur)
            continue
        s = line.strip()
        if not s or s.startswith("/*") or s == "*/":
            continue
        if cur is not None:
            cur["notes"].append(s.lstrip("-").strip().rstrip(";").strip())
    return out


def read_section(script: str, block_line: int | None = None) -> dict:
    """The entries of the notes block, for the register view."""
    secs = find_sections(script)
    if not secs:
        return {"found": False, "blocks": [], "entries": [], "ariadne_entries": []}
    sec = secs[0] if block_line is None else next((s for s in secs if s[0] == block_line), secs[0])
    lines = (script or "").split("\n")
    entries = parse_entries(lines[sec[0] + 1:sec[1]])
    return {"found": True,
            "blocks": [{"open_line": o, "close_line": c, "heading": h} for o, c, h in secs],
            "entries": entries, "ariadne_entries": [e for e in entries if e["via_ariadne"]]}


def propose(tool: str, actions: list, snapshot_names: list) -> str:
    """A note text for the dry run to show, one bullet per ';'. The user
    approves it by passing it back, as written or edited."""
    done = [a for a in actions if a.get("action") in ("CREATE", "UPDATE")]
    by = {}
    for a in done:
        by.setdefault(a["action"].lower() + "d master " + a["kind"] + "s", []).append(a["name"])
    parts = [f"{k}: {', '.join(sorted(set(v)))}" for k, v in by.items()] or ["no change"]
    for s in snapshot_names:
        parts.append(f"snapshot taken before the change: {s}")
    return "; ".join(parts)
