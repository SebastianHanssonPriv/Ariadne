"""Load-script facts. Comments once created phantom QVDs, $(vVar) paths broke the
link between producer and consumer, the commonest STORE form did not match at
all, LEFT JOIN was skipped, and an app named 'Extractor' that only read QVDs
counted as one."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _path  # noqa
from ariadne.qlik import script as S

ok = []


def case(label, script, stores, reads, unresolved=()):
    st, rd, un = S.parse_store_reads(script, with_unresolved=True)
    good = (st == set(stores) and rd == set(reads) and un == set(unresolved))
    ok.append(good)
    print(f"  {'ok  ' if good else 'FAIL'} {label}")
    if not good:
        print(f"       stores {sorted(st)} want {sorted(stores)}; reads {sorted(rd)} want {sorted(reads)}; "
              f"unresolved {sorted(un)} want {sorted(unresolved)}")


print("comments must not create phantom QVDs:")
case("// commented STORE and LOAD are ignored",
     "// STORE X INTO [lib://QVD/Old.qvd](qvd);\n// LOAD * FROM [lib://QVD/Legacy.qvd](qvd);\n"
     "T:\nSQL SELECT 1;\nSTORE T INTO [lib://QVD/Real.qvd](qvd);", ["real.qvd"], [])
case("/* block */ comment is ignored",
     "/*\nSTORE X INTO [lib://QVD/Old.qvd](qvd);\n*/\nT:\nLOAD * FROM [lib://QVD/Live.qvd](qvd);",
     [], ["live.qvd"])
case("REM ...; is ignored",
     "REM STORE X INTO [lib://QVD/Old.qvd](qvd);\nT:\nLOAD * FROM [lib://QVD/Live.qvd](qvd);",
     [], ["live.qvd"])
case("lib:// survives - its // is not a comment",
     "T:\nLOAD * FROM [lib://QVD_Prod/Live.qvd](qvd);", [], ["live.qvd"])

print("variable-built paths:")
case("SET with a trailing slash",
     "SET vQVD = 'lib://QVD_Prod/';\nT:\nSQL SELECT 1;\nSTORE T INTO [$(vQVD)Orders.qvd](qvd);",
     ["orders.qvd"], [])
case("a variable defined from another variable",
     "SET vRoot = 'lib://DL';\nSET vQVD = '$(vRoot)/QVD/';\nT:\nSQL SELECT 1;\n"
     "STORE T INTO [$(vQVD)Orders.qvd](qvd);", ["orders.qvd"], [])
case("a name built at runtime stays UNRESOLVED, not invented",
     "LET vTbl = Upper('orders');\nT:\nSQL SELECT 1;\nSTORE T INTO [lib://QVD/$(vTbl).qvd](qvd);",
     [], [], ["$(vtbl).qvd"])
case("a non-ASCII variable name resolves",
     "SET vÅr = 'lib://QVD/';\nT:\nSQL SELECT 1;\nSTORE T INTO [$(vÅr)Sales.qvd](qvd);",
     ["sales.qvd"], [])

print("STORE forms:")
case("bracketed path with (qvd) suffix", "STORE T INTO [lib://QVD/A.qvd](qvd);", ["a.qvd"], [])
case("bare path", "STORE T INTO lib://QVD/B.qvd (qvd);", ["b.qvd"], [])
case("an app that reads its own output is a producer, not a consumer",
     "T:\nSQL SELECT 1;\nSTORE T INTO [lib://QVD/Orders.qvd](qvd);\n"
     "U:\nLOAD * FROM [lib://QVD/Orders.qvd](qvd);", ["orders.qvd"], [])

print("tables and joins:")


def tcase(label, script, want):
    got = [(t["label"], {f["out"] for f in t["out_fields"]}) for t in S.parse_load_tables(script)]
    good = got == want
    ok.append(good)
    print(f"  {'ok  ' if good else 'FAIL'} {label}")
    if not good:
        print(f"       got {got}\n       want {want}")


for prefix in ("Left Join", "Inner Join", "Join"):
    tcase(f"'{prefix} (Item)' lands its fields in Item",
          "Item:\nLOAD ItemKey, ItemCode FROM [lib://D/Item.qvd](qvd);\n"
          f"{prefix} (Item)\nLOAD ItemKey, SupplierName FROM [lib://D/Supplier.qvd](qvd);",
          [("Item", {"ItemKey", "ItemCode"}), ("Item", {"ItemKey", "SupplierName"})])
tcase("'Left Keep (Sales)' keeps its own label",
      "Sales:\nLOAD ItemKey, Qty FROM [lib://D/Sales.qvd](qvd);\nLeft Keep (Sales)\nItem:\n"
      "LOAD ItemKey, ItemCode FROM [lib://D/Item.qvd](qvd);",
      [("Sales", {"ItemKey", "Qty"}), ("Item", {"ItemKey", "ItemCode"})])
tcase("a table loaded inside For Each ... Next is not glued to the loop line",
      "For Each vM in 'Jan','Feb'\nT:\nLOAD A FROM [lib://D/$(vM).qvd](qvd);\nNext",
      [("T", {"A"})])
tcase("preceding LOAD + SQL SELECT is one sql table",
      "LIB CONNECT TO 'ERP';\nOrders:\nLOAD OrderNo, Amount;\nSQL SELECT OrderNo, Amount FROM dbo.Orders;",
      [("Orders", {"OrderNo", "Amount"})])
t = S.parse_load_tables("LIB CONNECT TO 'ERP';\nOrders:\nLOAD OrderNo;\nSQL SELECT OrderNo FROM dbo.Orders;\nB:\nSQL SELECT X FROM dbo.B;")
ok.append(len(t) == 2 and t[1]["label"] == "B")
print(f"  {'ok  ' if ok[-1] else 'FAIL'} a bare SQL SELECT after that is still its own table ({len(t)} tables)")
good = t[0]["kind"] == "sql" and t[0]["connection"] == "ERP"
ok.append(good)
print(f"  {'ok  ' if good else 'FAIL'} the SQL table remembers its connection ({t[0]['kind']}, {t[0]['connection']!r})")

print("extractor classification is on behaviour:")
CASES = [
    ("SQL source, stores a QVD", "T:\nSQL SELECT * FROM dbo.Orders;\nSTORE T INTO [lib://QVD/Orders.qvd](qvd);", True),
    ("CSV drop, stores a QVD", "T:\nLOAD * FROM [lib://Drop/prices.csv] (txt);\nSTORE T INTO [lib://QVD/P.qvd](qvd);", True),
    ("transform layer: QVD in, QVD out", "T:\nLOAD * FROM [lib://QVD/O.qvd](qvd);\nSTORE T INTO [lib://QVD/A.qvd](qvd);", False),
    ("binary load then store", "BINARY [lib://Apps/Other.qvf];\nSTORE X INTO [lib://QVD/C.qvd](qvd);", False),
    ("reporting app: SQL, stores nothing", "T:\nSQL SELECT * FROM dbo.Orders;", False),
]
for label, script, want in CASES:
    got = bool(S.extractor_reason(S.facts_from_script(script)))
    ok.append(got == want)
    print(f"  {'ok  ' if got == want else 'FAIL'} {label:44} {'extractor' if got else 'not'}")
r = S.extractor_reason(S.UNREADABLE)
ok.append(r == "" and S.UNREADABLE["loads_external"] is None)
print(f"  {'ok  ' if ok[-1] else 'FAIL'} an unreadable script is unknown (loads_external None), never 'not an extractor'")

print("what leaves the process:")
red = S.redact_secrets("CONNECT TO 'Provider=SQLOLEDB;User ID=svc;Password=Hunter2;Data Source=x';")
ok.append("Hunter2" not in red and "Password=<redacted>" in red)
print(f"  {'ok  ' if ok[-1] else 'FAIL'} a password in a CONNECT string is masked: {red[:70]}")
inc = S.include_refs("$(Include=lib://Scripts/common.qvs);\n// $(Include=lib://Scripts/old.qvs);\n$(Must_Include=lib://S/b.qvs);")
ok.append(inc == ["lib://S/b.qvs", "lib://Scripts/common.qvs"])
print(f"  {'ok  ' if ok[-1] else 'FAIL'} include files are listed (live ones only): {inc}")
conns = S.connections_named("LIB CONNECT TO 'ERP_Prod';\nLOAD * FROM [lib://QVD_Layer/a.qvd](qvd);\n// LIB CONNECT TO 'Old';")
ok.append(conns == ["ERP_Prod", "QVD_Layer"])
print(f"  {'ok  ' if ok[-1] else 'FAIL'} connections named, comments excluded: {conns}")

print("inline rows are data and never leave the engine layer:")
inline = ("Map:\nMapping LOAD * INLINE [\nCode, Customer\nA, Acme AB\nB, Beta Oy\n];\n"
          "T:\nLOAD Code, Qty INLINE \"\nCode, Qty\n1, 2\n\";\n// LOAD * INLINE [\nX\nsecret row\n];\n"
          "U:\nLOAD * FROM [lib://Q/a.qvd](qvd);")
red = S.redact_inline(inline)
ok.append("Acme" not in red and "Beta" not in red and "secret row" not in red and "1, 2" not in red)
print(f"  {'ok  ' if ok[-1] else 'FAIL'} every inline row is withheld, in comments too")
ok.append("Code, Customer" in red and "Code, Qty" in red and "withheld: 2 line(s)" in red)
print(f"  {'ok  ' if ok[-1] else 'FAIL'} the header line and the row count are kept")
got = [(t["label"], t["kind"]) for t in S.parse_load_tables(red)]
ok.append(got == [("Map", "inline"), ("T", "inline"), ("U", "from")])
print(f"  {'ok  ' if ok[-1] else 'FAIL'} the parser still sees the inline tables: {got}")
ok.append(S.redact_inline("LOAD * FROM [lib://Q/a.qvd](qvd);") == "LOAD * FROM [lib://Q/a.qvd](qvd);")
print(f"  {'ok  ' if ok[-1] else 'FAIL'} a script without INLINE is untouched")
print()
print(f"{sum(ok)} of {len(ok)} pass")
sys.exit(0 if all(ok) else 1)
