"""The one shape every tool returns.

An MCP tool answer is read by a model that will repeat it to a person with
more confidence than it arrived with. So the answer must carry its own
qualifications, in fields the model cannot drop without noticing:

    data         what was found
    coverage     what was measured, as counts: asked / answered / refused /
                 failed, plus the scope the question was limited to
    unknowns     things the tenant did not say - each one a tri-state None in
                 the data, named here so "not reported" is never read as "no"
    limitations  what this method cannot see at all (a dynamic $(…) name, a
                 chart's raw column, an app in another user's Personal space)
    evidence     why a classification was made, so it can be audited rather
                 than trusted
    confidence   high / medium / low / none, set from coverage, never from
                 wording
    framing      a fixed sentence: this is a prioritised worklist to verify

Absence of evidence is never evidence. Sixteen review cycles of the
application this was ported from found that mistake eleven times, and every
one of them made a report state something false rather than state nothing.
"""
from __future__ import annotations

from typing import Any

FRAMING = ("Prioritised worklist to verify, not a verdict: text parsing cannot see "
           "inside a dynamic $(...) expression, and the Admin APIs do not expose "
           "everything a chart does.")

CONFIDENCE = ("high", "medium", "low", "none")


def coverage(asked: int, answered: int, refused: int = 0, failed: int = 0,
             scope: str = "", **extra: Any) -> dict:
    """Counts first. A percentage is derived here so no caller computes its own."""
    cov = {"asked": asked, "answered": answered, "refused": refused, "failed": failed,
           "scope": scope}
    cov["share_answered"] = (round(answered / asked, 4) if asked else None)
    cov.update(extra)
    return cov


def confidence_from(cov: dict | None) -> str:
    """Confidence follows coverage. Not the other way round, and never prose."""
    if not cov or cov.get("asked") in (None, 0):
        return "none"
    share = cov.get("share_answered")
    if share is None:
        return "none"
    if share >= 0.99:
        return "high"
    if share >= 0.8:
        return "medium"
    if share > 0:
        return "low"
    return "none"


def envelope(data: Any, *, coverage: dict | None = None, unknowns=(), limitations=(),
             evidence=(), confidence: str | None = None, access: dict | None = None,
             **extra: Any) -> dict:
    conf = confidence or confidence_from(coverage)
    assert conf in CONFIDENCE, conf
    out = {
        "data": data,
        "coverage": coverage or {},
        "unknowns": list(unknowns),
        "limitations": list(limitations),
        "evidence": list(evidence),
        "confidence": conf,
        "framing": FRAMING,
    }
    if access is not None:
        out["access"] = access
    out.update(extra)
    return out


def refused(what: str, rule: str, fix: str, detail: str = "") -> dict:
    """A permission problem is a finding, not an error. The caller still gets
    data=None, and the reason and the governed fix beside it."""
    return envelope(None, coverage=coverage(1, 0, refused=1, scope=what),
                    access={"state": "refused", "what": what, "rule": rule, "fix": fix,
                            "detail": detail},
                    limitations=[f"{what}: refused, so nothing below says anything about it."],
                    confidence="none")


def failed(what: str, error: str) -> dict:
    """A call that failed for a reason that says nothing about the object."""
    return envelope(None, coverage=coverage(1, 0, failed=1, scope=what),
                    access={"state": "failed", "what": what, "detail": error},
                    limitations=[f"{what}: the call failed ({error}); this is not evidence "
                                 "about the object."],
                    confidence="none")


SCRIPT_RULE = ("In Qlik Cloud, opening an app and reading its load script are two "
               "different rights. The script is given up to the app's OWNER, or to a "
               "user holding 'Can edit data in applications' in the shared space the "
               "app lives in, and to nobody else. Tenant Admin and Developer roles do "
               "not grant it.")
SCRIPT_FIX = ("Give this key's user 'Can edit data in applications' in the spaces that "
              "matter (the governed fix, once per space), or run with a key belonging "
              "to the user who owns the app. diagnose_app_access shows which layer "
              "refuses.")
