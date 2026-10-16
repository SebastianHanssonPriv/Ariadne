"""Every tool against a fake tenant. Pins: all tools are read-only; a refused
script is an access finding with data=None; a tenant-wide scan reports refused
scripts in its coverage and lowers confidence; the key never appears in any
result, including in an error the tenant echoes it back in."""
import asyncio, json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _path  # noqa
from ariadne.config import Settings
from ariadne.qlik.engine import EngineRefused, EngineError
from ariadne.qlik.rest import QlikHTTPError
from ariadne.qlik import bulk
from ariadne.server import build_server, Backend

KEY = "eyJSECRETKEY123456789abcdefghij.payload.sig"
SCRIPTS = {
    "ext": "LIB CONNECT TO 'ERP';\nOrders:\nLOAD OrderNo, Amount;\nSQL SELECT OrderNo, Amount FROM dbo.Orders;\n"
           "STORE Orders INTO [lib://QVD/Orders.qvd](qvd);",
    "rep": "SET vQVD = 'lib://QVD/';\nOrders:\nLOAD * FROM [$(vQVD)Orders.qvd](qvd);",
    "dyn": "LET vT = Upper('orders');\nT:\nLOAD * FROM [lib://QVD/$(vT).qvd](qvd);",
}
APPS = [
    {"name": "ABC Inventory QVD creator", "guid": "ext", "space_id": "s1", "owner_id": "u1",
     "reloaded": "2026-10-01T00:00:00Z", "published": None, "description": ""},
    {"name": "Sales Report", "guid": "rep", "space_id": "s1", "owner_id": "u2",
     "reloaded": "2026-10-02T00:00:00Z", "published": True, "description": ""},
    {"name": "Dynamic reader", "guid": "dyn", "space_id": "s2", "owner_id": "u1",
     "reloaded": "", "published": False, "description": ""},
    {"name": "Locked", "guid": "locked", "space_id": "s2", "owner_id": "u3",
     "reloaded": "2026-09-01T00:00:00Z", "published": True, "description": ""},
]


class FakeRest:
    def list_apps(self): return [dict(a) for a in APPS]
    def list_spaces(self): return {"s1": {"name": "Finance", "type": "shared", "owner_id": "u2"},
                                   "s2": {"name": "Prod", "type": "managed", "owner_id": "u3"}}
    def me(self): return {"id": "u1", "name": "svc", "roles": ["TenantAdmin"], "status": "active"}
    def user_info(self, uid):
        return {"u1": {"name": "svc", "status": "active", "found": True},
                "u2": {"name": "Gone", "status": "disabled", "found": True},
                "u3": {"name": "", "status": "", "found": None}}[uid]
    def app(self, guid):
        if guid == "missing":
            raise QlikHTTPError(404, "https://t/api/v1/apps/missing")
        a = next(x for x in APPS if x["guid"] == guid)
        return {"attributes": {"name": a["name"], "ownerId": a["owner_id"], "spaceId": a["space_id"],
                               "publishTime": "2026-01-01T00:00:00Z" if a["published"] else "",
                               "lastReloadTime": a["reloaded"]}, "privileges": ["read"]}
    def app_scripts(self, guid): raise QlikHTTPError(403, "https://t/api/v1/apps/x/scripts")
    def lineage_graph(self, guid):
        if guid != "rep":
            raise QlikHTTPError(404, "https://t/api/v1/lineage-graphs/nodes/x")
        return {"nodes": {"qri:app:sense://rep": {"label": "Sales Report"},
                          "qri:app:sense://ext": {"label": "ABC"},
                          "qri:qdf:space://s1#lib/Orders.qvd": {"label": "Orders.qvd", "type": "QVD"},
                          "qri:db:sqlserver://erp/dbo.Orders": {"label": "dbo.Orders"}},
                "edges": [{"source": "qri:qdf:space://s1#lib/Orders.qvd", "target": "qri:app:sense://rep", "relation": []}]}


class FakeEngine:
    def __init__(self, app_id): self.app_id = app_id
    def __enter__(self): return self
    def __exit__(self, *e): pass
    def layout(self): return {"qTitle": self.app_id}
    def get_script(self):
        if self.app_id == "locked":
            raise EngineRefused(f"Access denied (code 403) key={KEY}")
        return SCRIPTS[self.app_id]
    def measures(self):
        if self.app_id == "locked":
            raise EngineRefused(f"Access denied (code 403) key={KEY}")
        return {"ext": [{"id": "m1", "name": "Net Sales", "expression": "Sum([Amount])", "label_expression": ""}],
                "rep": [{"id": "m2", "name": "Net Sales", "expression": "Sum(Amount) // net", "label_expression": ""},
                        {"id": "m3", "name": "UK", "expression": "if(C='United Kingdom',1,0)", "label_expression": ""}],
                "dyn": [{"id": "m4", "name": "UK", "expression": "if(C='UnitedKingdom',1,0)", "label_expression": ""},
                        {"id": "m5", "name": "Dyn", "expression": "$(vExpr)", "label_expression": ""}]}.get(self.app_id, [])
    def dimensions(self): return []
    def variables(self): return [{"id": "v", "name": "vExpr", "definition": "Sum(Hidden)", "is_reserved": False,
                                  "is_script_created": False}]
    def model_fields(self): return [{"name": "Amount", "src_tables": ["Orders"], "is_system": False, "is_hidden": False, "is_key": False},
                                    {"name": "Unused", "src_tables": ["Orders"], "is_system": False, "is_hidden": False, "is_key": False},
                                    {"name": "Hidden", "src_tables": ["Orders"], "is_system": False, "is_hidden": False, "is_key": False}]
    def tables(self): return [{"name": "Orders", "rows": 10, "fields": 3, "is_synthetic": False}]
    def sheets(self): return [{"qInfo": {"qId": "sh1"}, "qData": {"title": "Main", "cells": []}}]
    def objects(self): return [{"sheet": "Main", "id": "o1", "type": "kpi", "measure_libs": ["m2"], "dim_libs": [],
                                "expressions": ["$(=Sum(' & 'Hidden' & ')')"]}]


class FakeBackend(Backend):
    def __init__(self):
        super().__init__(Settings(tenant="t.eu.qlikcloud.com", api_key=KEY, workers=2))
        self.rest = FakeRest()
    def engine(self, app_id): return FakeEngine(app_id)
    def read_script(self, app_id):
        try:
            return FakeEngine(app_id).get_script(), ""
        except EngineRefused as e:
            return None, f"refused: {e}"
    def facts_for(self, apps, on_progress=None):
        from ariadne.qlik import script as S
        out, errors, refused = {}, {}, 0
        for a in apps:
            s, err = self.read_script(a["guid"])
            if s is None:
                out[a["guid"]] = dict(S.UNREADABLE); errors[a["guid"]] = err; refused += 1
            else:
                out[a["guid"]] = S.facts_from_script(s)
        cov = {"asked": len(apps), "answered": len(apps) - refused, "refused": refused, "failed": 0,
               "cache_hits": 0, "systemic_stop": "", "failure_kinds": [(e, 1) for e in set(errors.values())]}
        return out, cov, errors


srv = build_server(backend=FakeBackend())
ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -> {detail}" if detail and not cond else ""))


def call(name, **args):
    res = asyncio.run(srv.call_tool(name, args))
    text = "".join(c.text for c in res.content if getattr(c, "type", "") == "text")
    check(f"[{name}] the key never appears in the result", KEY not in text and KEY[:24] not in text)
    return json.loads(text) if text.startswith("{") else text


tools = asyncio.run(srv.list_tools())
names = sorted(t.name for t in tools)
print("surface:")
check("13 tools registered", len(names) == 13, names)
check("every tool is annotated read-only", all(t.annotations and t.annotations.read_only_hint for t in tools))
check("every tool has a description", all((t.description or "").strip() for t in tools))

print("reach:")
r = call("tenant_overview")
check("apps counted by space type", r["data"]["apps"]["by_space_type"] == {"shared": 2, "managed": 2})
check("publish state is three states", r["data"]["apps"]["publish_state"] == {"published": 2, "not_published": 1, "not_reported": 1})
check("unknowns name the unreported publish state", any("publish state" in u for u in r["unknowns"]))
r = call("list_apps", published="unknown")
check("published='unknown' returns the app whose state was not reported", [a["guid"] for a in r["data"]["apps"]] == ["ext"])
r = call("describe_app", app_id="rep")
check("owner state travels with the app", r["data"]["owner"]["state"] == "risk")
check("model read", r["data"]["model"]["table_count"] == 1 and r["data"]["script"]["readable"] is True)
check("coverage is complete for a readable app", r["coverage"]["asked"] == 3 and r["coverage"]["answered"] == 3)
r = call("describe_app", app_id="locked")
check("a refused script is a finding, the app is still described", r["data"]["name"] == "Locked"
      and r["data"]["script"]["readable"] is False and r["data"]["script_access"]["state"] == "refused")
check("owner unverified when the lookup failed", r["data"]["owner"]["state"] == "unverified")
check("the refused model is a finding too, and coverage says 1 of 3", r["data"]["model"] is None
      and r["data"]["model_access"]["state"] == "refused" and r["coverage"]["refused"] == 2)
r = call("describe_app", app_id="missing")
check("a 404 is 'failed', data None, not an exception", r["data"] is None and r["access"]["state"] == "failed")
r = call("diagnose_app_access", app_id="locked")
check("diagnosis names the script rule", "OWNER" in r["data"]["conclusion"])

print("thread:")
r = call("get_load_script", app_id="locked")
check("refused script -> data None, access.state refused, rule present", r["data"] is None
      and r["access"]["state"] == "refused" and "owner" in r["access"]["rule"].lower())
r = call("get_load_script", app_id="ext", max_chars=20)
check("paging: truncated with next_offset", r["data"]["truncated"] and r["data"]["next_offset"] == 20)
r = call("analyze_load_script", script_text=SCRIPTS["ext"])
check("extractor found on behaviour", r["data"]["is_extractor"] is True and r["data"]["stores_qvd"] == ["orders.qvd"])
check("connections named, the QVD folder included", r["data"]["connections"] == ["ERP", "QVD"])
r = call("analyze_load_script", script_text=SCRIPTS["dyn"])
check("runtime name is unresolved, not invented", r["data"]["unresolved_names"] == ["$(vt).qvd"] and r["data"]["reads_files"] == [])
r = call("get_lineage_graph", app_id="rep")
check("graph summarised by kind", r["data"]["nodes_by_kind"].get("qvd") == 1 and r["data"]["upstream_apps"] == ["ext"])
r = call("get_lineage_graph", app_id="ext")
check("no graph -> failed, never 'no lineage'", r["data"] is None and r["access"]["state"] == "failed")
r = call("trace_qvd", qvd_name="Orders")
check("producer and consumer found through $(vVar)", [p["app_id"] for p in r["data"]["producers"]] == ["ext"]
      and [c["app_id"] for c in r["data"]["consumers"]] == ["rep"])
check("the runtime reader is possible, not confirmed", [p["app_id"] for p in r["data"]["possible_runtime_references"]] == ["dyn"])
check("the refused app is counted, listed, and lowers confidence", r["coverage"]["refused"] == 1
      and r["data"]["unread_count"] == 1 and r["confidence"] == "low")
check("unknowns say the unread app may read it", any("could not be read" in u for u in r["unknowns"]))
r = call("list_extractors")
check("one extractor, by evidence, with unknown count", [e["app_id"] for e in r["data"]["extractors"]] == ["ext"]
      and r["data"]["unknown_count"] == 1 and "SQL" in r["data"]["extractors"][0]["evidence"])

print("govern:")
r = call("compare_master_items", app_ids=["ext", "rep", "dyn", "locked"])
conf = {c["name"]: c["variant_count"] for c in r["data"]["measure_name_conflicts"]}
check("Net Sales agrees across apps; UK conflicts on a literal", conf == {"UK": 2}, conf)
check("the refused app counts as refused in coverage", r["coverage"]["refused"] == 1 and r["data"]["apps_compared"] == 3)
r = call("compare_master_items", app_ids=["ext"])
check("one app is not a comparison", r["data"] is None)
r = call("find_unused_in_app", app_id="rep")
check("Unused is a candidate, Amount and Hidden (via variable) are not",
      [f["name"] for f in r["data"]["unused_fields"]] == ["Unused"])
check("the dynamic expression is reported as hiding usage", r["data"]["counts"]["dynamic_expressions"] >= 1 and r["unknowns"])
r = call("check_ownership")
check("disabled owner's published app is at risk; unverified listed separately",
      [a["app_id"] for a in r["data"]["apps_at_risk"]][:1] == ["rep"]
      and any(a["owner_state"] == "unverified" for a in r["data"]["apps_at_risk"]))
check("coverage counts the failed lookup", r["coverage"]["failed"] == 1)

print("prompt and resource:")
prompts = asyncio.run(srv.list_prompts())
check("governance_review prompt exists", [p.name for p in prompts] == ["governance_review"])
res = asyncio.run(srv.read_resource("ariadne://principles"))
check("principles resource reads", any("Absence of evidence" in getattr(c, "content", "") for c in res))

print()
print(f"{sum(ok)} of {len(ok)} pass")
sys.exit(0 if all(ok) else 1)
