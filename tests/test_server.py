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
SCRIPTS["rep_pub"] = SCRIPTS["rep"]
ARCH, ARCH_FIN = "6238963d4769f1ced8bf9931", "67d04f9b700c02cbea1f67e8"
APPS = [
    {"name": "ABC Inventory QVD creator", "guid": "ext", "space_id": "s1", "owner_id": "u1",
     "reloaded": "2026-10-01T00:00:00Z", "published": None, "description": "", "publish_time": "", "origin_app_id": ""},
    {"name": "Sales Report", "guid": "rep", "space_id": "s1", "owner_id": "u2",
     "reloaded": "2026-10-02T00:00:00Z", "published": True, "description": "", "publish_time": "", "origin_app_id": ""},
    {"name": "Dynamic reader", "guid": "dyn", "space_id": "s2", "owner_id": "u1",
     "reloaded": "", "published": False, "description": "", "publish_time": "", "origin_app_id": ""},
    {"name": "Locked", "guid": "locked", "space_id": "s2", "owner_id": "u3",
     "reloaded": "2026-09-01T00:00:00Z", "published": True, "description": "", "publish_time": "", "origin_app_id": ""},
    {"name": "Sales Report", "guid": "rep_pub", "space_id": "m_fin", "owner_id": "u2",
     "reloaded": "2026-10-02T00:00:00Z", "published": True, "description": "",
     "publish_time": "2026-10-02T10:00:00Z", "origin_app_id": "rep"},
]


class FakeRest:
    def __init__(self):
        self.apps_list = [dict(a) for a in APPS]
        self.writes = []
    def list_apps(self): return [dict(a) for a in self.apps_list]
    def list_spaces(self): return {"s1": {"name": "Finance", "type": "shared", "owner_id": "u2"},
                                   "s2": {"name": "Prod", "type": "managed", "owner_id": "u3"},
                                   "m_fin": {"name": "Finance Reports", "type": "managed", "owner_id": "u2"},
                                   ARCH: {"name": "Archive", "type": "shared", "owner_id": "u1"},
                                   ARCH_FIN: {"name": "Archive - Finance", "type": "shared", "owner_id": "u1"}}
    def export_app(self, app_id, no_data=True):
        self.writes.append(("export", app_id, no_data)); return b"QVF"
    def import_app(self, qvf, name, space_id):
        self.writes.append(("import", name, space_id))
        new = {"name": name, "guid": f"snap{len(self.writes)}", "space_id": space_id, "owner_id": "u1", "reloaded": "",
               "published": None, "description": "", "publish_time": "", "origin_app_id": ""}
        self.apps_list.append(new)
        return {"id": new["guid"], "spaceId": space_id, "name": name}
    def move_app(self, app_id, space_id):
        self.writes.append(("move", app_id, space_id))
        for a in self.apps_list:
            if a["guid"] == app_id:
                a["space_id"] = space_id
    def set_app_description(self, app_id, d):
        self.writes.append(("describe", app_id))
        for a in self.apps_list:
            if a["guid"] == app_id:
                a["description"] = d
    def me(self): return {"id": "u1", "name": "svc", "roles": ["TenantAdmin"], "status": "active"}
    def user_info(self, uid):
        return {"u1": {"name": "svc", "status": "active", "found": True},
                "u2": {"name": "Gone", "status": "disabled", "found": True},
                "u3": {"name": "", "status": "", "found": None}}[uid]
    def app(self, guid):
        if guid == "missing":
            raise QlikHTTPError(404, "https://t/api/v1/apps/missing")
        a = next(x for x in self.apps_list if x["guid"] == guid)
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
    def bundle(self):
        return {"script": self.get_script(), "variables": self.variables(), "measures": self.measures(),
                "dimensions": self.dimensions(), "objects": self.objects()}
    engine_calls = []
    def open(self): return 1
    def call(self, h, method, params):
        from ariadne.qlik.policy import check_engine
        check_engine(method, True)
        FakeEngine.engine_calls.append((self.app_id, method))
        if method == "CreateSessionObject": return {"qReturn": {"qHandle": 99}}
        if method == "GetLayout": return {"qLayout": {"qMeasureList": {"qItems": [
            {"qInfo": {"qId": m["id"]}, "qMeta": {"title": m["name"]}} for m in self.measures()]},
            "qDimensionList": {"qItems": []}}}
        if method in ("GetMeasure", "GetDimension"): return {"qReturn": {"qHandle": 7}}
        if method == "GetProperties": return {"qProp": {"qInfo": {"qId": "x"}, "qMeasure": {}, "qMetaDef": {}}}
        return {"qReturn": {"qHandle": 0}}
    def do_save(self): FakeEngine.engine_calls.append((self.app_id, "DoSave"))


class FakeBackend(Backend):
    def __init__(self, allow_write=False):
        super().__init__(Settings(tenant="t.eu.qlikcloud.com", api_key=KEY, workers=2, allow_write=allow_write,
                                  archive_space_id=ARCH, archive_map={"*finance*": ARCH_FIN}))
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


backend = FakeBackend()
srv = build_server(backend=backend)
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
check("17 tools registered", len(names) == 17, names)
check("the four write-path tools are annotated as not read-only, the rest read-only",
      {t.name for t in tools if not t.annotations.read_only_hint} == {"snapshot_app", "archive_app", "apply_master_items"})
check("every tool carries annotations", all(t.annotations for t in tools))
check("every tool has a description", all((t.description or "").strip() for t in tools))

print("reach:")
r = call("tenant_overview")
check("apps counted by space type", r["data"]["apps"]["by_space_type"] == {"shared": 2, "managed": 3})
check("publish state is three states", r["data"]["apps"]["publish_state"] == {"published": 3, "not_published": 1, "not_reported": 1})
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
      and [c["app_id"] for c in r["data"]["consumers"]] == ["rep", "rep_pub"])
check("the runtime reader is possible, not confirmed", [p["app_id"] for p in r["data"]["possible_runtime_references"]] == ["dyn"])
check("the refused app is counted, listed, and lowers confidence", r["coverage"]["refused"] == 1
      and r["data"]["unread_count"] == 1 and r["confidence"] == "medium" and r["coverage"]["share_answered"] == 0.8)
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
      [a["app_id"] for a in r["data"]["apps_at_risk"]][:2] == ["rep_pub", "rep"]
      and any(a["owner_state"] == "unverified" for a in r["data"]["apps_at_risk"]))
check("coverage counts the failed lookup", r["coverage"]["failed"] == 1)

print("write path, writes disabled:")
r = call("snapshot_app", app_id="rep", dry_run=False)
check("snapshot refused when writes are disabled, nothing called", r["data"] is None and r["access"]["state"] == "refused"
      and not backend.rest.writes)
r = call("apply_master_items", app_id="rep", measures=[{"name": "Net Sales", "expression": "1"}], dry_run=False)
check("apply refused when writes are disabled", r["access"]["state"] == "refused" and not FakeEngine.engine_calls)

print("write path, writes enabled:")
wb = FakeBackend(allow_write=True)
wsrv = build_server(backend=wb)


def wcall(name, **args):
    res = asyncio.run(wsrv.call_tool(name, args))
    text = "".join(c.text for c in res.content if getattr(c, "type", "") == "text")
    check(f"[{name}] the key never appears in the result", KEY not in text and KEY[:24] not in text)
    return json.loads(text)


r = wcall("snapshot_app", app_id="rep")
it = r["data"]["plan"]["items"][0]
check("dry run plans a snapshot of the published copy into Archive - Finance, no data",
      r["data"]["dry_run"] and it["copy_of"] == "rep_pub" and it["archive_space_id"] == ARCH_FIN
      and it["with_data"] is False and it["needed"] and not wb.rest.writes)
r = wcall("snapshot_app", app_id="rep", archive_space_id="deadbeef")
check("an archive space outside the configured ones is refused", r["access"]["state"] == "refused")
r = wcall("apply_master_items", app_id="rep", measures=[{"name": "Net Sales", "expression": "Sum(Amount)"},
                                                        {"name": "net sales", "expression": "x"}], dry_run=False)
w = wb.rest.writes
check("snapshot taken first: export without data, import into Archive - Finance, marker written",
      w[0] == ("export", "rep_pub", True) and w[1][0] == "import" and w[1][2] == ARCH_FIN and w[2][0] == "describe")
check("then the write: update applied, near miss refused, saved once",
      r["data"]["measures"]["updated"] == 1 and r["data"]["measures"]["ambiguous"] == 1 and r["data"]["saved"]
      and [m for a, m in FakeEngine.engine_calls if m == "DoSave"] == ["DoSave"])
check("the snapshot is reported with the write", r["data"]["snapshot"]["taken"][0]["copy_of"] == "rep_pub")
n = len(wb.rest.writes)
r = wcall("apply_master_items", app_id="rep", measures=[{"name": "Net Sales", "expression": "Sum(Amount)"}], dry_run=False)
check("second write after the same publish takes no new snapshot", len(wb.rest.writes) == n
      and r["data"]["snapshot"]["plan"]["items"][0]["needed"] is False)
r = wcall("apply_master_items", app_id="ext", measures=[{"name": "X", "expression": "1"}], dry_run=True)
check("an app with no published copy: no snapshot, plan says so", r["data"]["snapshot"]["plan"]["applies"] is False)
r = wcall("apply_master_items", app_id="rep", measures=[{"name": "Net Sales"}], mode="delete")
check("mode=delete is refused by policy", r["access"]["state"] == "refused")
check("no Destroy method and no DELETE ever reached the fakes",
      not any(m.startswith("Destroy") and m != "DestroySessionObject" for _, m in FakeEngine.engine_calls)
      and not any(x[0] == "delete" for x in wb.rest.writes))
r = wcall("archive_app", app_id="dyn", dry_run=False)
check("archive_app moves to the default archive and verifies", r["data"]["moved"] and r["data"]["to_space_id"] == ARCH
      and ("move", "dyn", ARCH) in wb.rest.writes)
r = wcall("compare_app_versions", app_id="rep", other_app_id="ext")
check("compare reports script and measure differences", r["data"]["script"]["changed"]
      and "UK" in r["data"]["measures"]["removed"])

print("prompt and resource:")
prompts = asyncio.run(srv.list_prompts())
check("governance_review prompt exists", [p.name for p in prompts] == ["governance_review"])
res = asyncio.run(srv.read_resource("ariadne://principles"))
check("principles resource reads", any("Absence of evidence" in getattr(c, "content", "") for c in res))

print()
print(f"{sum(ok)} of {len(ok)} pass")
sys.exit(0 if all(ok) else 1)
