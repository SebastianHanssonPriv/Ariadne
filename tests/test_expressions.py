"""What counts as a reference, and when two calculations are the same. A stale
comment used to hide a live field; 'United Kingdom' and 'UnitedKingdom' used
to compare equal."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _path  # noqa
from ariadne.qlik import expressions as X

ok = []
print("references:")
for label, expr, field, want in [
    ("a bracketed field reference", "Sum([Region])", "region", True),
    ("a set-analysis modifier is a reference", "Sum({<Region={'North'}>} Sales)", "region", True),
    ("only in a line comment", "// TODO: add Region later", "region", False),
    ("only in a block comment", "Sum(Sales) /* Region split pending */", "region", False),
    ("only inside a string literal", "if(Country='Region',1,0)", "region", False),
    ("in a literal, but the expression is dynamic", "$(='if(X=' & 'Region' & ',1,0)')", "region", True),
    ("a non-ASCII bare name", "Sum(Försäljning)", "försäljning", True),
    ('a "quoted name" is one name', 'Sum("Net Sales")', "net sales", True),
]:
    got = field in X.referenced_names(expr)
    ok.append(got == want)
    print(f"  {'ok  ' if got == want else 'FAIL'} {label}")

print("comparison key, must be EQUAL:")
for label, a, b in [
    ("optional brackets round a plain identifier", "Sum([Sales])", "Sum(Sales)"),
    ("whitespace and case", "Sum( Sales )", "sum(sales)"),
    ("a trailing comment", "Sum(Sales) // net", "Sum(Sales)"),
    ('"Net Sales" and [Net Sales]', 'Sum("Net Sales")', "Sum([Net Sales])"),
    ("escaped ]] inside a name", "Sum([Rev]]EU])", 'Sum("Rev]EU")'),
]:
    good = X.tight(a) == X.tight(b)
    ok.append(good)
    print(f"  {'ok  ' if good else 'FAIL'} {label}")
print("comparison key, must DIFFER:")
for label, a, b in [
    ("whitespace inside a literal", "if(C='United Kingdom',1,0)", "if(C='UnitedKingdom',1,0)"),
    ("'X' string vs \"X\" field", "if(R='North',1,0)", 'if(R="North",1,0)'),
    ("different aggregation", "Sum(Sales)", "Avg(Sales)"),
    ("escaped '' does not end the string early", "if(X='it''s // here',1,0)", "if(X='it''s',1,0)"),
]:
    good = X.tight(a) != X.tight(b)
    ok.append(good)
    print(f"  {'ok  ' if good else 'FAIL'} {label}")
for label, v in (("empty", ""), ("None", None), ("unterminated quote", "if(X='abc"), ("unterminated bracket", "Sum([Sales")):
    try:
        X.tight(v); ok.append(True)
    except Exception as e:
        ok.append(False); print(f"  FAIL {label}: {e}")

print("end to end:")
measures = [
    {"name": "Net Sales", "expression": "Sum([Sales])", "app": "A"},
    {"name": "Net Sales", "expression": "Sum(Sales) // same", "app": "B"},
    {"name": "Turnover", "expression": "Sum(Sales)", "app": "C"},
    {"name": "UK flag", "expression": "if(Country='United Kingdom',1,0)", "app": "A"},
    {"name": "UK flag", "expression": "if(Country='UnitedKingdom',1,0)", "app": "B"},
]
res = X.analyze_consistency(measures, [])
conf = {c["name"]: c["variant_count"] for c in res["measure_name_conflicts"]}
ok.append(conf == {"UK flag": 2})
print(f"  {'ok  ' if ok[-1] else 'FAIL'} only the UK flag conflicts: {conf}")
red = [(r["names"], r["apps"]) for r in res["measure_redundancy"]]
ok.append(red == [(["Net Sales", "Turnover"], ["A", "B", "C"])])
print(f"  {'ok  ' if ok[-1] else 'FAIL'} Sum(Sales) is redundant under two names: {red}")
fields = [{"name": "Region", "src_tables": ["T"], "is_system": False, "is_hidden": False, "is_key": False},
          {"name": "Sales", "src_tables": ["T"], "is_system": False, "is_hidden": False, "is_key": False}]
u = X.analyze_usage([], [], [], [{"sheet": "S", "id": "o", "type": "kpi", "measure_libs": [], "dim_libs": [],
                                  "expressions": ["Sum([Sales])", "// Region is handled downstream"]}], fields)
ok.append([f["name"] for f in u["fields"]["unused"]] == ["Region"])
print(f"  {'ok  ' if ok[-1] else 'FAIL'} a field named only in a comment is an unused candidate")
print()
print(f"{sum(ok)} of {len(ok)} pass")
sys.exit(0 if all(ok) else 1)
