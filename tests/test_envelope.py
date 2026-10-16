"""The envelope and the tri-state fields. The source application once scored an
unreported publish state as 'not published' and a missing reload as a penalty."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _path  # noqa
from ariadne import envelope as E
from ariadne.config import load_settings, scrub, ConfigError
from ariadne.qlik.rest import app_from_item
from ariadne.qlik import ownership as O

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  ({detail})" if detail and not cond else ""))


print("tri-state fields from the Items API:")
a = app_from_item({"resourceId": "g1", "name": "A", "resourceAttributes": {}})
check("no publish field at all -> None, not False", a["published"] is None)
a = app_from_item({"resourceId": "g2", "name": "B", "resourceAttributes": {"published": False}})
check("published: false -> False", a["published"] is False)
a = app_from_item({"resourceId": "g3", "name": "C", "resourceAttributes": {"publishTime": "2026-01-01T00:00:00Z"}})
check("publishTime present -> True", a["published"] is True)
a = app_from_item({"resourceId": "g4", "name": "D", "resourceAttributes": {"published": None}})
check("published: null -> None", a["published"] is None)
check("no reload time -> empty string, not a date", app_from_item({"resourceId": "g5", "name": "E"})["reloaded"] == "")
check("an item without an id is dropped, not invented", app_from_item({"name": "ghost"}) is None)

print("confidence follows coverage:")
check("nothing asked -> none", E.confidence_from(E.coverage(0, 0)) == "none")
check("all answered -> high", E.confidence_from(E.coverage(100, 100)) == "high")
check("85% -> medium", E.confidence_from(E.coverage(100, 85, refused=15)) == "medium")
check("10% -> low", E.confidence_from(E.coverage(100, 10, refused=90)) == "low")
check("0 of 100 -> none", E.confidence_from(E.coverage(100, 0, refused=100)) == "none")
env = E.envelope({"x": 1}, coverage=E.coverage(10, 10))
check("every envelope carries the framing", env["framing"] == E.FRAMING)
for k in ("data", "coverage", "unknowns", "limitations", "evidence", "confidence"):
    check(f"envelope has {k}", k in env)

print("a refusal is a finding, not an error:")
r = E.refused("app X script", E.SCRIPT_RULE, E.SCRIPT_FIX, "Access denied")
check("data is None", r["data"] is None)
check("access.state is refused", r["access"]["state"] == "refused")
check("the rule and the fix travel with it", r["access"]["rule"] == E.SCRIPT_RULE and r["access"]["fix"] == E.SCRIPT_FIX)
check("confidence is none", r["confidence"] == "none")
check("coverage counts the refusal", r["coverage"]["refused"] == 1 and r["coverage"]["answered"] == 0)

print("ownership scoring: a gap is never a penalty:")
s0, why0 = O.priority({"published": None, "reloaded": ""}, "")
s1, _ = O.priority({"published": False, "reloaded": ""}, "")
check("unreported publish state scores the same as not published (0)", s0 == s1 == 0, f"{s0} vs {s1}")
check("and says so", "not reported" in why0)
check("a failed lookup is UNVERIFIED", O.classify({"found": None})[0] == O.UNVERIFIED)
check("a 404 is RISK (evidence)", O.classify({"found": False})[0] == O.RISK)
check("active is ACTIVE", O.classify({"found": True, "status": "active"})[0] == O.ACTIVE)

print("the key:")
try:
    load_settings({"QLIK_TENANT": "x.eu.qlikcloud.com"})
    check("missing key raises ConfigError", False)
except ConfigError as e:
    check("missing key raises ConfigError naming the variable", "QLIK_API_KEY" in str(e))
s = load_settings({"QLIK_TENANT": "https://x.eu.qlikcloud.com/", "QLIK_API_KEY": "eyJabc.def.ghi\n"})
check("key is stripped where it enters", s.api_key == "eyJabc.def.ghi")
check("tenant is normalised to a host", s.host == "x.eu.qlikcloud.com")
check("scrub removes the key from an error", "eyJabc" not in scrub("Invalid header value b'Bearer eyJabc.def.ghi\\n'", s.api_key))
print()
print(f"{sum(ok)} of {len(ok)} pass")
sys.exit(0 if all(ok) else 1)
