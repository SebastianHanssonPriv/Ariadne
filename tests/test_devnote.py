"""The developer note in its real shape: a /* ... */ block of entries
'- YYYY-MM-DD - Name L.:' with tab-indented bullets, oldest first, the new
entry appended before '*/'. Refused without a block or with several, and
the script that goes back is the old script plus the entry and nothing else."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _path  # noqa
from ariadne.qlik import devnote as DN
from ariadne.qlik.policy import PolicyError

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -> {detail}" if detail and not cond else ""))


REAL = """///$tab Change log
/*
/*
- 2026-04-27 - Robert J.:
\t- adding rebates tbl and calculation to Net Sales (for Timco); 
\t- adding Item Segment to DimItem (to use in the front filter); 
    - adding CustomerGroup (to use in the front filter);
- 2026-05-10 - Sebastian H.:
\t- Added export section to get table with data for Benchmark. Tested Data and Model after. Confirmed non-invasive.



*/
///$tab Main
SET vA=1;
/* a normal block comment, not notes */
T:
LOAD 1 as A AUTOGENERATE 1;"""

print("finding the block:")
secs = DN.find_sections(REAL)
check("the real example is one notes block, the ordinary block comment is not", len(secs) == 1 and secs[0][0] == 1 and secs[0][1] == 12)
check("its heading is the tab name", secs[0][2] == "///$tab Change log")
noheading = "/*\n- 2026-01-02 - Anna B.:\n\t- first;\n*/\nLOAD 1 as A AUTOGENERATE 1;"
check("a block with an entry needs no heading", len(DN.find_sections(noheading)) == 1)
empty = "// ## Developer notes\n/*\n*/\nLOAD 1 as A AUTOGENERATE 1;"
check("an empty block under a heading counts (hashes allowed)", len(DN.find_sections(empty)) == 1)
for h in ("// Change log", "//### developer log", "/* Developer notes */", "// # Dev notes"):
    check(f"{h!r} is a heading", DN.find_headings("x;\n" + h) == [(1, h)])
check("'LOAD ChangeLogId' is not a heading", DN.find_headings("LOAD ChangeLogId FROM x;") == [])

print("the entry:")
check("author tag: first name and surname initial", DN.author_tag("Sebastian Hansson") == "Sebastian H."
      and DN.author_tag("Robert Jönsson-Berg") == "Robert J." and DN.author_tag("svc") == "svc" and DN.author_tag("", "u1") == "u1")
entry = DN.format_entry("updated master measures: Net Sales; snapshot taken before the change: Sales 2026-10-16",
                        "Sebastian H.", "apply_master_items", "2026-10-16")
check("header line in the house form, stamped via Ariadne",
      entry[0] == "- 2026-10-16 - Sebastian H. (via Ariadne, apply_master_items):")
check("one tab-indented bullet per ';', each ending with ';'",
      entry[1:] == ["\t- updated master measures: Net Sales;", "\t- snapshot taken before the change: Sales 2026-10-16;"])
try:
    DN.format_entry(" ; ", "S", "t"); check("empty note -> refused", False)
except PolicyError:
    check("empty note -> refused", True)

print("insert:")
new, pos, head = DN.insert(REAL, entry)
lines = new.split("\n")
check("appended at the bottom of the block, before */, after the older entries",
      pos == 12 and lines[12] == entry[0] and lines[15] == "*/" and lines[7] == "- 2026-05-10 - Sebastian H.:")
check("verify_insert agrees", DN.verify_insert(REAL, new, entry) == 12)
try:
    DN.insert("LOAD 1 as A AUTOGENERATE 1;", entry); check("no block -> refused", False)
except PolicyError as e:
    check("no block -> refused, names the shape", "- 2026-05-10 - Name L.:" in str(e))
two = noheading + "\n/*\n- 2026-02-02 - Carl D.:\n\t- second;\n*/"
try:
    DN.insert(two, entry); check("two blocks -> refused", False)
except PolicyError as e:
    check("two blocks -> refused, lists the openers", "[0, 5]" in str(e))
new2, pos2, _ = DN.insert(two, entry, block_line=5)
check("two blocks, one chosen", pos2 == 8)
sameline = "/*\n- 2026-01-02 - Anna B.:\n\t- first; */\nLOAD 1 as A AUTOGENERATE 1;"
try:
    DN.insert(sameline, entry); check("'*/' sharing a line with text -> refused, not rewritten", False)
except PolicyError as e:
    check("'*/' sharing a line with text -> refused, not rewritten", "own line" in str(e))

print("the guard:")
for label, bad in (("a line changed elsewhere", new.replace("SET vA=1;", "SET vA=2;")),
                   ("a line dropped", "\n".join(lines[:-1])),
                   ("the entry inserted twice", "\n".join(lines[:12] + entry + lines[12:])),
                   ("the whole script replaced", "\n".join(entry))):
    try:
        DN.verify_insert(REAL, bad, entry); check(f"{label} -> refused", False)
    except PolicyError:
        check(f"{label} -> refused", True)

print("the register:")
sec = DN.read_section(new)
check("three entries parsed with dates, authors and bullets",
      [e["date"] for e in sec["entries"]] == ["2026-04-27", "2026-05-10", "2026-10-16"]
      and sec["entries"][0]["author"] == "Robert J." and len(sec["entries"][0]["notes"]) == 3
      and sec["entries"][1]["notes"][0].startswith("Added export section"))
check("Ariadne's entry is flagged, the others are not",
      [e["via_ariadne"] for e in sec["entries"]] == [False, False, True] and len(sec["ariadne_entries"]) == 1)
check("no block -> found False", DN.read_section("LOAD 1 as A AUTOGENERATE 1;")["found"] is False)
prop = DN.propose("apply_master_items", [{"action": "UPDATE", "kind": "measure", "name": "Net Sales"},
                                          {"action": "CREATE", "kind": "dimension", "name": "Geo"},
                                          {"action": "AMBIGUOUS", "kind": "measure", "name": "x"}],
                  ["Sales 2026-10-16 (published 2026-10-02)"])
check("proposed note: one bullet per change kind, the snapshot, nothing about the refused item",
      prop == "updated master measures: Net Sales; created master dimensions: Geo; "
              "snapshot taken before the change: Sales 2026-10-16 (published 2026-10-02)")
print()
print(f"{sum(ok)} of {len(ok)} pass")
sys.exit(0 if all(ok) else 1)
