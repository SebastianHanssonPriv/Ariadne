"""The policy under every call: DELETE is never sent, Destroy* is never sent,
writes need the flag. These are the rules a key cannot enforce for itself."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _path  # noqa
from ariadne.qlik import policy as P
from ariadne.qlik.rest import Rest
from ariadne.qlik.engine import EngineSession

ok = []


def check(label, cond):
    ok.append(bool(cond))
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")


def refused(fn, *a):
    try:
        fn(*a)
        return False
    except P.PolicyError:
        return True


print("HTTP:")
check("GET always allowed", not refused(P.check_http, "GET", False))
check("DELETE refused even with writes enabled", refused(P.check_http, "DELETE", True))
check("POST refused without the flag", refused(P.check_http, "POST", False))
check("PUT allowed with the flag", not refused(P.check_http, "PUT", True))
r = Rest("t.eu.qlikcloud.com", "k", allow_write=True)
check("Rest refuses DELETE before any network call", refused(r._raw, "DELETE", "/api/v1/apps/x"))
r0 = Rest("t.eu.qlikcloud.com", "k", allow_write=False)
check("a read-only Rest refuses PUT before any network call", refused(r0._raw, "PUT", "/api/v1/apps/x/space", {}))

print("Engine:")
check("GetScript allowed", not refused(P.check_engine, "GetScript", False))
for m in ("DestroyMeasure", "DestroyDimension", "DestroyVariable", "DestroyBookmark", "DeleteApp",
          "RemoveAlternateState", "ClearAll", "DeleteScript"):
    check(f"{m} is not on the allowlist, writes enabled", refused(P.check_engine, m, True))
check("DestroySessionObject is allowed (a session object is not an entity)",
      not refused(P.check_engine, "DestroySessionObject", False))
check("CreateMeasure refused without the flag", refused(P.check_engine, "CreateMeasure", False))
check("CreateMeasure allowed with the flag", not refused(P.check_engine, "CreateMeasure", True))
e = EngineSession("t.eu.qlikcloud.com", "k", "app", allow_write=True)
check("EngineSession.call refuses DestroyMeasure before touching the socket", refused(e.call, 1, "DestroyMeasure", ["x"]))
check("SetScript refused without the flag", refused(P.check_engine, "SetScript", False))
check("SetScript allowed with the flag (only through set_script_inserted)", not refused(P.check_engine, "SetScript", True))
check("set_script_inserted refuses a change that is not the inserted note, before the socket",
      refused(e.set_script_inserted, "A\nB", "A\nX\nB", ["Y"]))
print("metadata only, by construction:")
for m in ("GetHyperCubeData", "GetListObjectData", "GetFieldValues", "GetHyperCubeStackData", "ExportData",
          "GetTableData", "SearchValues", "GetAssociativeInsights", "SelectValues", "GetHyperCubeContinuousData"):
    check(f"{m} is never sent", refused(P.check_engine, m, True))
check("a metadata list session object is allowed",
      not refused(P.check_engine, "CreateSessionObject", False, [{"qInfo": {"qType": "qFieldList"}, "qFieldListDef": {}}]))
check("a hypercube session object is refused",
      refused(P.check_engine, "CreateSessionObject", False,
              [{"qInfo": {"qType": "x"}, "qHyperCubeDef": {"qDimensions": [], "qMeasures": []}}]))
check("a metadata list that smuggles a list object is refused",
      refused(P.check_engine, "CreateSessionObject", False,
              [{"qInfo": {"qType": "qFieldList"}, "qFieldListDef": {}, "qListObjectDef": {"qDef": {"qFieldDefs": ["Customer"]}}}]))
check("CreateSessionObject without a definition is refused", refused(P.check_engine, "CreateSessionObject", False, []))
check("an allowed REST path passes", not refused(P.check_path, "/api/v1/apps/x/scripts"))
check("an absolute temp-contents URL passes", not refused(P.check_path, "https://t.eu.qlikcloud.com/api/v1/temp-contents/abc?inline=1"))
for path in ("/api/v1/data-files/x", "/api/v1/datasets/x/data", "/api/v1/qix-datafiles/x", "/api/v1/apps-data", "/api/v1/reloads"):
    check(f"{path} is refused", refused(P.check_path, path))
check("Rest.get refuses a path outside the allowlist before any network call",
      refused(r0.get, "/api/v1/data-files/abc"))
print()
print(f"{sum(ok)} of {len(ok)} pass")
sys.exit(0 if all(ok) else 1)
