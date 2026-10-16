"""Load-script facts: what an app writes, reads, and loads from outside Qlik.

Comments are not code, and strings are not code. Three parsers in the source
application had to learn this separately, so every entry point here strips
comments first (keeping the `//` inside `lib://`), resolves `$(vVar)` paths
where the variable is SET to a literal, and reports a name still holding `$(`
after resolution as UNRESOLVED - never invented into a filename.

Bump FACTS_VERSION when any parser below changes: cached facts are keyed on it.
"""
from __future__ import annotations

import os
import re

FACTS_VERSION = 5   # 5: a preceding LOAD and its SQL SELECT are one table, not two


# ----------------------------------------------------------------- comments
def strip_script_comments(script: str) -> str:
    """Remove /* */ blocks, REM ...; and // line comments - but keep the
    '//' inside lib:// / http:// paths."""
    if not script:
        return ""
    s = re.sub(r"/\*.*?\*/", " ", script, flags=re.S)
    s = re.sub(r"(?im)\bREM\b[^;]*;", " ", s)
    out = []
    for line in s.split("\n"):
        m = re.search(r"(?<!:)//", line)
        out.append(line[:m.start()] if m else line)
    return "\n".join(out)


# ---------------------------------------------------------------- variables
def resolve_script_vars(script: str, passes: int = 3) -> str:
    """Substitute $(vName) where the script SETs vName to a plain literal.

    Only literal assignments are substituted. A LET whose value contains "("
    is an expression and expanding it as text would invent a filename, so it
    is left alone - a name we cannot resolve must stay visibly unresolved.
    """
    if not script or "$(" not in script:
        return script or ""
    pairs = {}
    for m in re.finditer(r"""(?im)^\s*(?:SET|LET)\s+([^\W\d][\w.]*)\s*=\s*(.*?);\s*$""", script):
        name, val = m.group(1), m.group(2).strip()
        val = val.strip("'\"")
        if "(" in re.sub(r"\$\([^\W\d][\w.]*\)", "", val):
            continue
        pairs[name.lower()] = val
    if not pairs:
        return script
    out = script
    for _ in range(passes):
        before = out

        def sub(m):
            return pairs.get(m.group(1).lower(), m.group(0))

        out = re.sub(r"\$\(([^\W\d][\w.]*)\)", sub, out)
        if out == before:
            break
    return out


_UNRESOLVED = re.compile(r"\$\(")


def extract_file_refs(text: str, with_unresolved: bool = False):
    """Basenames (lower-cased) of the data files a script mentions."""
    if not text:
        return (set(), set()) if with_unresolved else set()
    clean = resolve_script_vars(strip_script_comments(text))
    found = re.findall(r"([^\s\\/\[\]'\"]+\.(?:qvd|csv|txt|xlsx|xls|parquet))", clean, re.I)
    names, unresolved = set(), set()
    for f in found:
        base = os.path.basename(f).lower()
        if not base:
            continue
        (unresolved if _UNRESOLVED.search(base) else names).add(base)
    return (names, unresolved) if with_unresolved else names


def parse_store_reads(script: str, with_unresolved: bool = False):
    """(stores, reads[, unresolved]): QVD basenames the app writes
    (STORE ... INTO ...qvd) and QVDs it reads (everything else)."""
    if not script:
        return ((set(), set(), set()) if with_unresolved else (set(), set()))
    clean = resolve_script_vars(strip_script_comments(script))
    stores, unresolved = set(), set()
    for m in re.finditer(r"(?is)\bSTORE\b[^;]*?\bINTO\b\s*(\[[^\]]*\]|[^\s;]+)", clean):
        path = m.group(1).strip()
        if path.startswith("["):
            path = path[1:-1] if path.endswith("]") else path[1:]
        else:
            path = re.sub(r"(?i)\((?:qvd|txt|csv|parquet)\)\s*$", "", path)
        base = os.path.basename(path.strip()).strip("[]' ").replace('"', "").lower()
        if not base.endswith(".qvd"):
            continue
        (unresolved if _UNRESOLVED.search(base) else stores).add(base)
    all_qvd, unres_refs = extract_file_refs(script, with_unresolved=True)
    all_qvd = {b for b in all_qvd if b.endswith(".qvd")}
    reads = all_qvd - stores
    if with_unresolved:
        return stores, reads, unresolved | {u for u in unres_refs if u.endswith(".qvd")}
    return stores, reads


# ------------------------------------------------------------- statements
def _split_statements(script: str):
    """Split on ';', honouring [...] and quotes so embedded ';' do not split."""
    stmts, buf = [], []
    depth = 0
    quote = None
    for ch in script:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            continue
        if ch == "[":
            depth += 1
            buf.append(ch)
            continue
        if ch == "]":
            depth = max(0, depth - 1)
            buf.append(ch)
            continue
        if ch == ";" and depth == 0:
            s = "".join(buf).strip()
            if s:
                stmts.append(s)
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


def _split_top_level(s: str, sep: str = ","):
    parts, buf = [], []
    depth = 0
    quote = None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def _clean_ident(tok: str) -> str:
    t = (tok or "").strip()
    if len(t) >= 2 and t[0] in "[\"'" and t[-1] in "]\"'":
        t = t[1:-1]
    return t.strip()


def _field_in_out_name(piece: str):
    """(out_name, in_name) for one LOAD field expression. in_name is None when
    the left side is a real expression rather than a plain source field."""
    m = None
    for mm in re.finditer(r"(?i)\bas\b", piece):
        before = piece[:mm.start()]
        if before.count("(") == before.count(")"):
            m = mm
    if m:
        lhs = piece[:m.start()]
        rhs = piece[m.end():]
        out = _clean_ident(_split_top_level(rhs)[0])
        lhs_str = lhs.strip()
        if re.fullmatch(r"[\[\"']?[\w. ]+[\]\"']?", lhs_str):
            return out, _clean_ident(lhs_str)
        return out, None
    bare = _clean_ident(piece)
    return bare, (bare or None)


_SRC_KW = re.compile(r"(?i)\b(FROM|RESIDENT|INLINE|AUTOGENERATE)\b")

# Control statements end at the line break, not at ';'. Left in, a 'For Each'
# line glues itself onto the LOAD after it and every table loaded in a loop
# silently vanishes from the parse.
_CONTROL_LINE = re.compile(
    r"(?i)^\s*(?:for\s+each\b.*|for\b.+\bto\b.*|next\b.*|do(?:\s+(?:while|until)\b.*)?|loop\b.*"
    r"|if\b.+\bthen\s*|elseif\b.+\bthen\s*|else\s*|end\s*if\s*|sub\s+\S.*|end\s*sub\s*"
    r"|switch\b.*|case\b.*|default\s*|end\s*switch\s*|call\s+\S.*|exit\s+(?:for|do|sub|script)\b.*)$")

_LOAD_PREFIX = re.compile(
    r"(?is)^\s*(?P<kw>(?:NO)?CONCATENATE|(?:(?:LEFT|RIGHT|INNER|OUTER)\s+)?(?:JOIN|KEEP)"
    r"|MAPPING|BUFFER|ADD(?:\s+ONLY)?|REPLACE(?:\s+ONLY)?|SEMANTIC|HIERARCHY|GENERIC)\b"
    r"(?P<arg>\s*\([^)]*\))?\s*(?P<rest>.*)$")

_TABLE_LABEL = re.compile(
    r"""(?s)^\s*(\[[^\]]+\]|"[^"]+"|'[^']+'|[^\s:;,()\[\]"'=$]+)\s*:\s*(.*)$""")

_CONNECT = re.compile(r"(?is)\bCONNECT\s+TO\s+('([^']+)'|\[([^\]]+)\]|\"([^\"]+)\"|([^\s;(]+))")


def _bracket_delta(line: str) -> int:
    d, quote = 0, None
    for ch in line:
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "[":
            d += 1
        elif ch == "]":
            d -= 1
    return d


def _break_control_statements(script: str) -> str:
    out, depth = [], 0
    for line in script.split("\n"):
        if depth == 0 and _CONTROL_LINE.match(line):
            out.append(";")
            continue
        out.append(line)
        depth = max(0, depth + _bracket_delta(line))
    return "\n".join(out)


def _take_label(body: str):
    lm = _TABLE_LABEL.match(body)
    if lm and re.search(r"(?is)\bLOAD\b|\bSQL\b", lm.group(2) or ""):
        return _clean_ident(lm.group(1)), lm.group(2)
    return "", body


def _sql_select_fields(sql_text: str):
    m = re.search(r"(?is)^\s*(?:SQL\s+)?SELECT\s+(.*?)\s+FROM\b", sql_text or "")
    if not m:
        return [], False
    sel = m.group(1)
    if _split_top_level(sel)[0].strip() == "*":
        return [], True
    out_fields = []
    for piece in _split_top_level(sel):
        p = piece.strip()
        if not p or p == "*":
            continue
        out, inn = _field_in_out_name(p)
        if out:
            out_fields.append({"out": out, "in": inn})
    return out_fields, False


def _sql_source_label(sql_text: str, conn: str) -> str:
    mfrom = re.search(r"(?is)\bFROM\b\s+([\w.\[\]\"`]+)", sql_text or "")
    tbl = _clean_ident(mfrom.group(1)) if mfrom else ""
    conn = (conn or "").strip()
    if conn and tbl:
        return f"{conn} · {tbl}"
    if tbl:
        return "Database table " + tbl
    if conn:
        return "Database " + conn
    return "Database (SQL SELECT)"


def _implicit_table_name(t: dict) -> str:
    """Qlik names an unlabeled LOAD's table after its FROM source."""
    label = t.get("label") or ""
    if not label.startswith("(table #"):
        return label
    files = sorted(t.get("files") or [])
    if files:
        return os.path.splitext(files[0])[0]
    return label


def parse_load_tables(script: str):
    """The load script as table-producing LOAD/SQL statements:
      {label, out_fields:[{out,in}], wildcard, kind: from|resident|inline|
       autogenerate|sql|unknown, source, files:set, external:bool, connection}

    label is the table the rows land in, as Qlik decides it: a JOIN or
    CONCATENATE lands in its (target) or the previous table; KEEP stays its own.
    """
    clean = _break_control_statements(strip_script_comments(script or ""))
    stmts = _split_statements(clean)
    tables = []
    cur_conn = ""
    prev_label = ""
    consumed = set()   # SQL statements already attached to the LOAD before them
    for idx, st in enumerate(stmts):
        if idx in consumed:
            continue
        cm = _CONNECT.search(st)
        if cm:
            cur_conn = next((g for g in cm.groups()[1:] if g), "") or cur_conn
        label, body = _take_label(st)
        merge, target, mapping = "", "", False
        while True:
            pm = _LOAD_PREFIX.match(body)
            if not pm:
                break
            kw = pm.group("kw").upper().split()[-1]
            if kw in ("JOIN", "KEEP", "CONCATENATE") and not merge:
                merge = kw
                if pm.group("arg"):
                    target = _clean_ident(pm.group("arg").strip("() \t\r\n"))
            mapping = mapping or kw == "MAPPING"
            body = pm.group("rest")
            late, body = _take_label(body)
            label = label or late
        lm2 = re.match(r"(?is)^\s*LOAD\b(.*)$", body)
        is_sql = bool(re.match(r"(?is)^\s*SQL\b", body)) or bool(re.match(r"(?is)^\s*SELECT\b", body))
        if not lm2 and not is_sql:
            continue
        if merge in ("JOIN", "CONCATENATE"):
            label = target or prev_label or label

        kind, source, files, external = "unknown", "", set(), False
        out_fields, wildcard = [], False
        conn_used = ""

        if lm2:
            rest = lm2.group(1)
            km = _SRC_KW.search(rest)
            field_part = rest[:km.start()] if km else rest
            if "*" in _split_top_level(field_part)[0] and re.search(r"(?<![\w])\*", field_part):
                wildcard = True
            for piece in _split_top_level(field_part):
                p = piece.strip()
                if not p or p == "*":
                    continue
                out, inn = _field_in_out_name(p)
                if out:
                    out_fields.append({"out": out, "in": inn})
            if km:
                kw = km.group(1).lower()
                tail = rest[km.end():].strip()
                if kw == "from":
                    ref = re.split(r"(?is)\bwhere\b|\bwhile\b", tail)[0]
                    source = " ".join(ref.split())[:300]
                    files = set(extract_file_refs(ref))
                    kind, external = "from", True
                elif kw == "resident":
                    source = _clean_ident(re.split(r"(?is)\bwhere\b|\border\b", tail)[0])
                    kind, external = "resident", False
                elif kw == "inline":
                    source = "INLINE data"
                    kind, external = "inline", False
                elif kw == "autogenerate":
                    source = "AUTOGENERATE"
                    kind, external = "autogenerate", False
            else:
                nxt = stmts[idx + 1] if idx + 1 < len(stmts) else ""
                ncm = _CONNECT.search(nxt)
                ncon = (next((g for g in ncm.groups()[1:] if g), "") if ncm else "") or cur_conn
                if re.search(r"(?is)^\s*(SQL\b|SELECT\b)", nxt):
                    kind, external = "sql", True
                    source = _sql_source_label(nxt, ncon)
                    conn_used = ncon
                    consumed.add(idx + 1)
        elif is_sql:
            kind, external = "sql", True
            source = _sql_source_label(body, cur_conn)
            conn_used = cur_conn
            out_fields, wildcard = _sql_select_fields(body)

        tables.append({
            "label": label or (f"(table #{len(tables) + 1})"),
            "out_fields": out_fields,
            "wildcard": wildcard,
            "kind": kind, "source": source, "files": files,
            "external": external, "connection": conn_used,
        })
        if not mapping:
            prev_label = _implicit_table_name(tables[-1])
    return tables


def connections_named(script: str):
    """Every data connection the script names in a CONNECT TO or lib:// path.
    Names only: a connection string is where service accounts live, so the
    rest of the statement is never returned."""
    clean = strip_script_comments(script or "")
    out = set()
    for m in _CONNECT.finditer(clean):
        name = next((g for g in m.groups()[1:] if g), "")
        if name.lower().startswith("lib://"):
            name = name[6:].split("/")[0]
        if name:
            out.add(name)
    for m in re.finditer(r"(?i)lib://([^/\]'\"\s]+)", clean):
        out.add(m.group(1))
    return sorted(out)


def classify_external_load(script: str):
    """(loads_external, source_kind), tri-state:
      True  -> loads external data (SQL/database, or a non-QVD file)
      False -> QVD/file-only or binary load
      None  -> file-based, cannot tell from text (review)"""
    s = script or ""
    tables = parse_load_tables(s)
    if any(t.get("kind") == "sql" for t in tables):
        return True, "external DB (SQL)"
    if re.search(r"(?im)^\s*binary\b", s):
        return False, "binary load"
    froms = [t for t in tables if t.get("kind") == "from"]
    if not froms:
        return False, "no external load"
    if all(all(str(f).endswith(".qvd") for f in (t.get("files") or [])) for t in froms):
        return False, "QVD/file only"
    return None, "file-based (review)"


_SECRET = re.compile(r"(?i)\b(password|pwd|secret|token|api[_-]?key|client[_-]?secret)\s*=\s*([^;'\"\s\]]+)")
_INCLUDE = re.compile(r"(?i)\$\((?:must_)?include\s*=\s*([^)]*)\)")


def redact_secrets(script: str) -> str:
    """Mask credential-looking values in CONNECT strings before a script
    leaves the process. Old-style CONNECT TO strings can carry a password in
    clear; a tool result is read by a model and often logged by its client."""
    return _SECRET.sub(lambda m: f"{m.group(1)}=<redacted>", script or "")


def include_refs(script: str):
    """Files pulled in with $(Include=...) / $(Must_Include=...). Their text
    is NOT in the script this tool sees, so anything they load or store is
    invisible here - a limitation to state, never silently."""
    return sorted({m.group(1).strip() for m in _INCLUDE.finditer(strip_script_comments(script or ""))})


def facts_from_script(script: str) -> dict:
    """The small fact set tenant-wide questions need from one load script."""
    stores, reads, unresolved = parse_store_reads(script, with_unresolved=True)
    files, _u = extract_file_refs(script, with_unresolved=True)
    reads = files - stores
    loads_external, source_kind = classify_external_load(script)
    return {
        "stores": stores, "reads": reads,
        "loads_external": loads_external, "source_kind": source_kind,
        "unresolved": unresolved,
    }


UNREADABLE = {"stores": set(), "reads": set(), "loads_external": None,
              "source_kind": "unread", "unresolved": set()}


def extractor_reason(f: dict | None) -> str:
    """Why these facts make the app an extractor (loads from outside Qlik AND
    stores a QVD), or "" - which for unread facts means UNKNOWN, not "no".
    Classify on behaviour, not on names: the real tenant's extractors are
    called 'QVD creator'."""
    if not f or f.get("source_kind") == "unread":
        return ""
    if not f["stores"]:
        return ""
    if f["loads_external"] is False:
        return ""
    n = len(f["stores"])
    return f"{f['source_kind']}, stores {n} QVD{'' if n == 1 else 's'}"
