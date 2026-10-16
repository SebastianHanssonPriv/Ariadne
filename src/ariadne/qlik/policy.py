"""Which tenant operations Ariadne may perform. Checked under every call.

An API key cannot be scoped by Qlik, so these rules live here, below the
tools. HTTP: GET always; POST, PUT and PATCH only when writes are enabled;
DELETE never. Engine: an allowlist of read methods, a second allowlist of
write methods, and nothing else, so no Destroy* method can ever be sent.
"""
from __future__ import annotations

HTTP_READ = frozenset({"GET"})
HTTP_WRITE = frozenset({"POST", "PUT", "PATCH"})
HTTP_NEVER = frozenset({"DELETE"})

ENGINE_READ = frozenset({
    "OpenDoc", "GetAppLayout", "GetScript", "CreateSessionObject", "GetLayout",
    "DestroySessionObject",   # a session object is not a tenant entity
    "GetMeasure", "GetDimension", "GetProperties", "GetTablesAndKeys",
    "GetObject", "GetFullPropertyTree", "GetLineage", "GetVariableByName",
})
ENGINE_WRITE = frozenset({"CreateMeasure", "CreateDimension", "SetProperties", "DoSave"})


class PolicyError(RuntimeError):
    """The call was refused by Ariadne's own policy, before reaching the tenant."""


def check_http(method: str, allow_write: bool) -> None:
    m = (method or "GET").upper()
    if m in HTTP_READ:
        return
    if m in HTTP_NEVER:
        raise PolicyError(f"HTTP {m} is never sent by Ariadne: nothing is deleted.")
    if m in HTTP_WRITE:
        if not allow_write:
            raise PolicyError(f"HTTP {m} refused: writes are disabled (ARIADNE_ALLOW_WRITE is not 1).")
        return
    raise PolicyError(f"HTTP {m} is not a method Ariadne uses.")


def check_engine(method: str, allow_write: bool) -> None:
    if method in ENGINE_READ:
        return
    if method in ENGINE_WRITE:
        if not allow_write:
            raise PolicyError(f"Engine {method} refused: writes are disabled (ARIADNE_ALLOW_WRITE is not 1).")
        return
    raise PolicyError(f"Engine {method} is not on Ariadne's allowlist; it is not sent.")
