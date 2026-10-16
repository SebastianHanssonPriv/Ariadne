"""Entry points.

    ariadne                         serve over stdio (what an MCP client starts)
    ariadne serve --http --port N   serve over streamable HTTP (one tenant, one key)
    ariadne check                   test the configuration against the tenant
    ariadne analyze FILE.qvs        load-script facts for a file, no tenant needed
"""
from __future__ import annotations

import argparse
import json
import sys


def _check() -> int:
    from .config import ConfigError, load_settings
    from .qlik.rest import Rest
    try:
        s = load_settings()
    except ConfigError as e:
        print(f"not configured: {e}", file=sys.stderr)
        return 2
    r = Rest(s.tenant, s.api_key)
    me = r.me()
    spaces = r.list_spaces()
    print(f"tenant {s.host}: acting as {me.get('name') or '(unknown)'} ({me.get('id') or 'id not reported'}), "
          f"{len(spaces)} space(s) visible, workers={s.workers}")
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
    sub.add_parser("check", help="test the configuration against the tenant")
    an = sub.add_parser("analyze", help="load-script facts for a .qvs file")
    an.add_argument("file")
    args = p.parse_args(argv)
    if args.cmd == "check":
        return _check()
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
