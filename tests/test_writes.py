"""apply_master against a stubbed engine: duplicate titles acted on in full,
near misses refused, dry run exact, and no delete mode at all."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _path  # noqa
from ariadne.qlik import writes as W
from ariadne.qlik.policy import PolicyError, check_engine

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -> {detail}" if detail and not cond else ""))


class Fake:
    def __init__(self, titles):
        self.titles, self.calls = titles, []
    def open(self): return 1
    def call(self, h, method, params):
        check_engine(method, True, params)
        self.calls.append((method, params))
        if method == "CreateSessionObject": return {"qReturn": {"qHandle": 99}}
        if method == "GetLayout": return {"qLayout": {"qMeasureList": {"qItems": [
            {"qInfo": {"qId": i}, "qMeta": {"title": t}} for t, i in self.titles]}}}
        if method in ("GetMeasure", "GetDimension"): return {"qReturn": {"qHandle": 7}}
        if method == "GetProperties": return {"qProp": {"qInfo": {"qId": "x"}, "qMeasure": {"qDef": "old"}, "qMetaDef": {}}}
        return {"qReturn": {"qHandle": 0}}


def run(titles, rows, mode, dry=False):
    e = Fake(titles)
    r = W.apply_master(e, "measure", rows, mode, dry)
    return r, [p for m, p in e.calls if m == "CreateMeasure"], [p for m, p in e.calls if m == "SetProperties"], e


r, creates, sets, e = run([("Net Sales", "id1"), ("Net Sales", "id2")], [{"name": "Net Sales", "expression": "Sum(Sales)"}], "upsert")
check("duplicate titles: both updated, and the action says why", r["counts"]["updated"] == 2 and len(sets) == 2
      and "share" in r["actions"][0]["detail"])
check("shallow merge keeps unmentioned properties", sets[0][0]["qMeasure"]["qDef"] == "Sum(Sales)" and "qInfo" in sets[0][0])
r, creates, sets, e = run([("Net Sales", "id1")], [{"name": "net sales", "expression": "Sum(Sales)"}], "upsert")
check("near miss: refused, nothing written", r["counts"]["ambiguous"] == 1 and not creates and not sets
      and r["actions"][0]["action"] == "AMBIGUOUS")
r, creates, sets, e = run([("Net Sales", "id1")], [{"name": "Net Sales", "expression": "Sum(Sales)"}], "upsert")
check("exact match: one update", r["counts"]["updated"] == 1 and r["counts"]["ambiguous"] == 0)
r, creates, sets, e = run([("Net Sales", "id1")], [{"name": "Gross Margin", "expression": "Sum(M)", "tags": "finance, kpi"}], "upsert")
check("new name: created once with tags", r["counts"]["created"] == 1 and len(creates) == 1
      and creates[0][0]["qMetaDef"]["tags"] == ["finance", "kpi"])
r, creates, sets, e = run([], [{"name": "New One", "expression": "Sum(A)"}, {"name": "New One", "expression": "Sum(B)"}], "upsert")
check("same new name twice: created then updated", r["counts"]["created"] == 1 and r["counts"]["updated"] == 1)
r, creates, sets, e = run([("Net Sales", "id1"), ("Net Sales", "id2")], [{"name": "Net Sales", "expression": "x"}], "upsert", dry=True)
check("dry run: reports two updates, calls nothing", r["counts"]["updated"] == 2 and not sets and not creates
      and all(m in ("CreateSessionObject", "GetLayout", "DestroySessionObject") for m, _ in e.calls))
r, creates, sets, e = run([("A", "id1")], [{"name": "A", "expression": "1"}], "create")
check("mode=create skips an existing title", r["counts"]["skipped"] == 1)
r, creates, sets, e = run([], [{"name": "A", "expression": "1"}], "update")
check("mode=update skips a missing title", r["counts"]["skipped"] == 1)
try:
    run([("A", "id1")], [{"name": "A"}], "delete"); check("mode=delete does not exist", False)
except PolicyError:
    check("mode=delete does not exist", True)
check("no Destroy method was ever sent", not any(m.startswith("Destroy") and m != "DestroySessionObject" for m, _ in e.calls))
d, meta = W.build_dimension({"name": "Geo", "fields": "Country; City", "field_labels": ["Land", "Stad"], "grouping": "H"})
check("dimension fields from a ';' string, labels from a list", d["qFieldDefs"] == ["Country", "City"] and d["qFieldLabels"] == ["Land", "Stad"] and d["qGrouping"] == "H")
print()
print(f"{sum(ok)} of {len(ok)} pass")
sys.exit(0 if all(ok) else 1)
