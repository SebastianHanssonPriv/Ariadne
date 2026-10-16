"""Snapshot before write: routing by rule, refused when ambiguous, once per
publish, from the published copy, without data unless listed."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _path  # noqa
from ariadne.config import Settings, load_settings
from ariadne.qlik import snapshot as SN
from ariadne.qlik.policy import PolicyError

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  -> {detail}" if detail and not cond else ""))


ARCH, ARCH_FIN = "6238963d4769f1ced8bf9931", "67d04f9b700c02cbea1f67e8"
st = load_settings({"QLIK_TENANT": "t", "QLIK_API_KEY": "k", "ARIADNE_ALLOW_WRITE": "1",
                    "ARIADNE_ARCHIVE_SPACE_ID": ARCH, "ARIADNE_ARCHIVE_MAP": f"*finance*={ARCH_FIN}",
                    "ARIADNE_SNAPSHOT_WITH_DATA": "inputapp,*writeback*"})
print("configuration:")
check("archive map parsed", st.archive_map == {"*finance*": ARCH_FIN})
check("with-data list parsed", st.snapshot_with_data == ("inputapp", "*writeback*"))

print("routing:")
check("Finance space -> Archive - Finance", SN.route("m1", "Finance Reports", st)[0] == ARCH_FIN)
check("other space -> Archive", SN.route("m2", "Sales", st)[0] == ARCH)
check("explicit archive accepted when configured", SN.route("m2", "Sales", st, ARCH_FIN)[0] == ARCH_FIN)
try:
    SN.route("m2", "Sales", st, "deadbeef"); check("explicit non-archive space refused", False)
except PolicyError:
    check("explicit non-archive space refused", True)
amb = Settings(tenant="t", api_key="k", archive_space_id=ARCH, archive_map={"*finance*": ARCH_FIN, "*reports*": "x3"})
try:
    SN.route("m1", "Finance Reports", amb); check("two matching rules -> refused, not guessed", False)
except PolicyError as e:
    check("two matching rules -> refused, not guessed", "several" in str(e))
try:
    SN.route("m1", "Sales", Settings(tenant="t", api_key="k")); check("no archive configured -> refused", False)
except PolicyError:
    check("no archive configured -> refused", True)

print("plan:")
src = {"guid": "src", "name": "Sales", "space_id": "sh", "publish_time": "", "origin_app_id": "", "description": ""}
pub = {"guid": "pub", "name": "Sales", "space_id": "m1", "publish_time": "2026-10-02T10:00:00Z", "origin_app_id": "src", "description": ""}
spaces = {"sh": {"name": "Sales dev", "type": "shared"}, "m1": {"name": "Finance Reports", "type": "managed"},
          ARCH: {"name": "Archive", "type": "shared"}, ARCH_FIN: {"name": "Archive - Finance", "type": "shared"}}
pl = SN.plan(src, [src], spaces, st, today="2026-10-16")
check("no published copy -> does not apply", pl["applies"] is False)
pl = SN.plan(src, [src, pub], spaces, st, today="2026-10-16")
it = pl["items"][0]
check("one subject: the published copy, not the source", pl["applies"] and len(pl["items"]) == 1 and it["copy_of"] == "pub")
check("routed on the published copy's space", it["archive_space_id"] == ARCH_FIN)
check("name carries the date and the publish date", it["name"] == "Sales 2026-10-16 (published 2026-10-02)", it["name"])
check("without data by default", it["with_data"] is False)
check("needed", it["needed"] is True)
snap = {"guid": "snap1", "name": it["name"], "space_id": ARCH_FIN, "publish_time": "", "origin_app_id": "",
        "description": SN.marker("src", "pub", pub["publish_time"], "2026-10-16T08:00:00Z", False) + "\nold text"}
pl = SN.plan(src, [src, pub, snap], spaces, st, today="2026-10-17")
check("a snapshot for this publish exists -> not needed", pl["items"][0]["needed"] is False
      and pl["items"][0]["existing_snapshot_id"] == "snap1")
pub2 = dict(pub, publish_time="2026-10-20T09:00:00Z")
pl = SN.plan(src, [src, pub2, snap], spaces, st, today="2026-10-21")
check("a new publish -> needed again", pl["items"][0]["needed"] is True)
pl = SN.plan(pub, [src, pub], spaces, st, today="2026-10-16")
check("writing to the published copy itself snapshots it", pl["applies"] and pl["items"][0]["copy_of"] == "pub")
inp = dict(src, guid="inputapp")
pubi = dict(pub, guid="pubi", origin_app_id="inputapp")
pl = SN.plan(inp, [inp, pubi], spaces, st, today="2026-10-16")
check("an app on the with-data list is snapshotted with data", pl["items"][0]["with_data"] is True)
m = SN.parse_marker(snap["description"])
check("marker round-trips", m and m["source"] == "src" and m["copy_of"] == "pub" and m["with_data"] is False)
check("a description without a marker is not a snapshot", SN.parse_marker("just text") is None)

print("execute:")


class R:
    def __init__(self, land_in):
        self.calls, self.land_in = [], land_in
    def export_app(self, app_id, no_data=True):
        self.calls.append(("export", app_id, no_data)); return b"QVF"
    def import_app(self, qvf, name, space_id):
        self.calls.append(("import", name, space_id)); return {"id": "new1", "spaceId": self.land_in, "name": name}
    def move_app(self, app_id, space_id):
        self.calls.append(("move", app_id, space_id)); self.land_in = space_id
    def app(self, app_id):
        return {"attributes": {"spaceId": self.land_in}}
    def set_app_description(self, app_id, d):
        self.calls.append(("describe", app_id, d[:30]))


r = R(ARCH_FIN)
res = SN.execute(r, src, it, "orig")
check("export without data, import into the archive, marker set",
      r.calls[0] == ("export", "pub", True) and r.calls[1][2] == ARCH_FIN and r.calls[-1][0] == "describe"
      and res["snapshot_id"] == "new1")
check("no move when the import landed in the archive", not any(c[0] == "move" for c in r.calls))
r = R("personal")
res = SN.execute(r, src, it, "")
check("landed elsewhere -> moved, then verified", any(c == ("move", "new1", ARCH_FIN) for c in r.calls))


class Stuck(R):
    def move_app(self, app_id, space_id):
        self.calls.append(("move", app_id, space_id))   # does not land


try:
    SN.execute(Stuck("personal"), src, it, ""); check("could not be placed -> PolicyError, write refused", False)
except PolicyError as e:
    check("could not be placed -> PolicyError naming the copy, nothing deleted", "new1" in str(e))
print()
print(f"{sum(ok)} of {len(ok)} pass")
sys.exit(0 if all(ok) else 1)
