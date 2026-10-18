"""Entry points.

    ariadne                         serve over stdio (what an MCP client starts)
    ariadne serve --http --port N   serve over streamable HTTP (one tenant, one key)
    ariadne check [--app ID]        test the configuration against the tenant; with
                                    --app, also open that app over the Engine and
                                    try to read its script
    ariadne analyze FILE.qvs        load-script facts for a file, no tenant needed
"""
from __future__ import annotations

import argparse
import json
import sys


def _check(app_id: str = "") -> int:
    import os
    from .config import ConfigError, load_settings, scrub
    from .qlik.engine import EngineRefused, EngineSession, proxy_settings
    from .qlik.rest import Rest
    try:
        s = load_settings()
    except ConfigError as e:
        print(f"not configured: {e}", file=sys.stderr)
        return 2
    px = proxy_settings(s.host)
    print(f"proxy for Engine: {px.get('http_proxy_host') + ':' + str(px.get('http_proxy_port')) if px else 'none (direct)'}; "
          f"REST follows HTTPS_PROXY={'set' if os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy') else 'unset'}")
    r = Rest(s.tenant, s.api_key)
    try:
        me = r.me()
        spaces = r.list_spaces()
    except Exception as e:
        print(f"REST failed: {scrub(str(e), s.api_key)}", file=sys.stderr)
        return 1
    print(f"REST ok: tenant {s.host}, acting as {me.get('name') or '(unknown)'} ({me.get('id') or 'id not reported'}), "
          f"{len(spaces)} space(s) visible, workers={s.workers}, writes={'on' if s.allow_write else 'off'}, "
          f"audit log={s.audit_log or 'per-user default'}")
    if not app_id:
        print("add --app <app id> to test the Engine path (WebSocket) on one app")
        return 0
    eng = EngineSession(s.tenant, s.api_key, app_id, allow_write=False)
    try:
        eng.connect()
        title = eng.layout().get("qTitle", app_id)
        print(f"Engine ok: opened '{title}'")
        try:
            script = eng.get_script()
            print(f"script ok: {len(script.splitlines())} line(s), inline rows withheld")
        except EngineRefused as e:
            print(f"script refused: {scrub(str(e), s.api_key)}; this key's user is not the owner and holds no "
                  "'Can edit data in applications' role in the app's space")
    except Exception as e:
        print(f"Engine failed: {scrub(str(e), s.api_key)}", file=sys.stderr)
        return 1
    finally:
        eng.close()
    return 0


def _analyze(path: str) -> int:
    from .qlik import script as S
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    facts = S.facts_from_script(text)
    out = {
        "tables": [{"label": t["label"], "kind": t["kind"], "source": S.redact_secrets(t["source"]),
                    "files": sorted(t["files"]), "fields": [x["out"] for x in t["out_fields"]],
                    "wildcard": t["wildcard"]} for t in S.parse_load_tables(text)],
        "stores_qvd": sorted(facts["stores"]), "reads_files": sorted(facts["reads"]),
        "unresolved_names": sorted(facts["unresolved"]),
        "loads_external": facts["loads_external"], "source_kind": facts["source_kind"],
        "extractor_evidence": S.extractor_reason(facts),
        "connections": S.connections_named(text), "include_files": S.include_refs(text),
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ariadne", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    sv = sub.add_parser("serve", help="run the MCP server (default)")
    sv.add_argument("--http", action="store_true", help="streamable HTTP instead of stdio")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)
    ck = sub.add_parser("check", help="test the configuration against the tenant")
    ck.add_argument("--app", default="", help="also open this app over the Engine and read its script")
    an = sub.add_parser("analyze", help="load-script facts for a .qvs file")
    an.add_argument("file")
    args = p.parse_args(argv)
    if args.cmd == "check":
        return _check(args.app)
    if args.cmd == "analyze":
        return _analyze(args.file)
    from .server import build_server
    srv = build_server()
    if args.cmd == "serve" and args.http:
        srv.settings.host = args.host
        srv.settings.port = args.port
        srv.run(transport="streamable-http")
    else:
        srv.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
