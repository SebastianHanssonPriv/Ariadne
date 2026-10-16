# Ariadne

A governance-grade [MCP](https://modelcontextprotocol.io) server for Qlik Cloud.
It answers the questions a BI governance team asks across a whole tenant: who
owns what, where a QVD comes from and who reads it, whether two apps define
"Net Sales" the same way, what a load script actually does. Every answer carries
its own coverage, unknowns and limitations, so the model reading it cannot
quietly turn "not measured" into "no".

Ariadne is the thread through the labyrinth, not the labyrinth. It is read-only,
runs where you run it, and complements Qlik's official MCP server rather than
replacing it (see [Beside the official server](#beside-the-official-qlik-mcp-server)).

```text
Claude Desktop / Claude Code / any MCP client
        |  stdio (or streamable HTTP)
   ariadne  (Python, this repository)
        |  REST + Engine API, one API key, read-only
   Qlik Cloud tenant
```

## What it answers

| Group | Tool | Question it answers |
|---|---|---|
| Reach | `tenant_overview` | What can this key see: spaces, apps by space type, publish state as three states, blind spots. Call first. |
| | `list_apps` | Apps with owner, space, publish state (true, false, or not reported) and last reload. Filterable, paged. |
| | `describe_app` | One app: owner and their account status, space type, reload, data model, and whether the script is readable. |
| | `diagnose_app_access` | Which layer lets this key see an app and which refuses, with the rule and the governed fix. |
| Thread | `get_load_script` | The load script, paged, with credential-looking values masked. |
| | `analyze_load_script` | The script as facts: tables, sources, connections, QVDs stored and read, unresolved run-time names, external load (true, false, or cannot tell), extractor evidence. Works on text you paste too. |
| | `get_lineage_graph` | Qlik's own lineage graph for an app, whole and uncollapsed, summarised by node kind. |
| | `trace_qvd` | Which apps write a QVD and which read it, from every script this key can read, with coverage. |
| | `list_extractors` | Apps that load from outside Qlik and store a QVD, classified on behaviour with evidence. |
| Govern | `get_master_items` | Master measures, dimensions and variables of one app. |
| | `compare_master_items` | Across apps: same name, different calculation (risk) and same calculation, different names (redundancy). |
| | `find_unused_in_app` | Fields, master items and variables nothing on any sheet references. Lists dynamic expressions that hide usage. |
| | `check_ownership` | Content whose owner is disabled, deleted, missing or unverifiable, by priority, plus owner concentration. |

Plus one prompt, `governance_review`, that runs these in an order that keeps
coverage honest, and one resource, `ariadne://principles`.

## The envelope

Every tool returns the same shape:

```json
{
  "data": { "...": "what was found" },
  "coverage": { "asked": 1908, "answered": 1611, "refused": 290, "failed": 7, "share_answered": 0.8443, "scope": "load scripts of 1908 app(s)" },
  "unknowns": [ "297 app(s) whose script could not be read may write or read orders.qvd; nothing is known about them" ],
  "limitations": [ "Apps in another user's Personal space are not listed by the Items API ..." ],
  "evidence": [ "2 producer(s) by STORE ... INTO, 41 consumer(s) by file reference, over 1611 readable script(s) of 1908" ],
  "confidence": "medium",
  "framing": "Prioritised worklist to verify, not a verdict: ..."
}
```

`confidence` is set from `coverage`, never from wording. A permission problem
comes back as `access: {state: "refused", rule, fix}` with `data: null`: a
finding about the key, not a fact about the object. The numbers above are
illustrative of the shape, not measurements.

## Setup

Requirements: Python 3.10 or newer, a Qlik Cloud API key for the user Ariadne
should act as. Read access is enough; Ariadne never writes.

```bash
pip install .                     # or: pip install -e . while developing
export QLIK_TENANT=acme.eu.qlikcloud.com
export QLIK_API_KEY=...           # or QLIK_API_KEY_FILE=/path/to/key
ariadne check                     # who the key acts as, how many spaces it sees
ariadne analyze some_script.qvs   # load-script facts, no tenant needed
```

| Variable | Meaning | Default |
|---|---|---|
| `QLIK_TENANT` | tenant host | required |
| `QLIK_API_KEY` or `QLIK_API_KEY_FILE` | the key, or a file whose first line is the key | required |
| `ARIADNE_WORKERS` | concurrent engine sessions for tenant-wide reads | 6 |
| `ARIADNE_SCRIPT_MAX_CHARS` | characters of script returned per call | 60000 |

### Claude Desktop

Add to `claude_desktop_config.json` (see `examples/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "ariadne": {
      "command": "ariadne",
      "env": { "QLIK_TENANT": "acme.eu.qlikcloud.com", "QLIK_API_KEY_FILE": "/Users/me/.qlik/key" }
    }
  }
}
```

### Claude Code

Copy `examples/mcp.json` to `.mcp.json` in the project, or run:

```bash
claude mcp add ariadne -e QLIK_TENANT=acme.eu.qlikcloud.com -e QLIK_API_KEY_FILE=~/.qlik/key -- ariadne
```

The skill in `skills/ariadne-qlik-governance/` follows the
[Agent Skills](https://agentskills.io/specification) format Qlik's own skills
use; copy it to `.claude/skills/` to give the agent the review workflow.

### Streamable HTTP

```bash
ariadne serve --http --port 8765
```

One process serves one tenant with one key. There is no per-user identity on
this transport yet; put it behind your own authentication or keep it on stdio.
See `docs/DESIGN.md` for the planned OAuth path.

## Beside the official Qlik MCP server

Qlik's server (help pages dated 2026-10-15) is the right tool for analysis,
selections, chart building, automations, glossary and data products. Ariadne
does none of that. It exists for the tenant-wide, evidence-weighted questions
the official catalogue answers one object at a time or not at all.

| | Official Qlik MCP server | Ariadne |
|---|---|---|
| Hosting | Remote, by Qlik, at `https://<tenant>.<region>.qlikcloud.com/api/ai/mcp` | Local process (stdio) or your own HTTP host |
| Identity | OAuth 2.0 as the connecting user (CIMD, DCR or a manual client; M2M web client for orchestration). Requires the `Qlik MCP: Allowed` role permission | One API key, acting as its user. Read-only |
| Cost | Every 5 billable tool calls consume 1 question from the monthly quota shared with Qlik Answers | None beyond the tenant's API rate limits |
| Guardrails | 200 concurrent app sessions per tenant, 300 tool calls per user per minute | Worker count you set; gzip, Retry-After on 429/503, no retry on 404 |
| Scope of a call | One app, one dataset, one lineage step (`qlik_get_lineage` is called recursively) | One app, or every app this key can read, with coverage reported |
| Script | `qlik_get_script` returns text; `qlik_update_script` writes it | Text, paged and masked; and the script as facts with comments stripped and `$(vVar)` resolved |
| Lineage | One step upstream per call, from Qlik's lineage service | Whole graph uncollapsed, plus a producer/consumer map built from scripts, with unresolved names kept apart |
| Master items | List, create, update, delete (update and delete only for MCP-created items) | List, and compare across apps on a quote-aware key |
| Ownership, unused items, extractors | Not in the catalogue | Yes, as prioritised worklists with evidence |
| Uncertainty | Tool-specific; the official `qlik-ai-readiness-optimizer` skill itself warns that "absence of readable data isn't evidence of absence" | Every result: coverage counts, unknowns, limitations, confidence from coverage |
| Writes | Many (apps, scripts, reloads, automations, glossary, data products, bookmarks) | None in this version |

Use both: the official server for doing, Ariadne for knowing how far the doing
can be trusted.

## Principles

`CLAUDE.md` is the rule sheet. The short form: absence of evidence is never
evidence; say what you did not measure; classify on behaviour, not names;
comments are not code; totals are computed where the full data is; a report is
a worklist to verify; permission is a finding; the key never leaves the process.

## Tests

```bash
python tests/run_all.py
```

Five suites, no framework. Each pins a defect that was real in the application
these parsers were ported from, and prints what the defect was.

## Status

Version 0.1.0: a read-only first iteration. `docs/DESIGN.md` holds the gap
analysis against the official server, the decisions taken, the ones still open,
and the roadmap.

## License and provenance

Copyright Sebastian Hansson, Apache License 2.0 (see `LICENSE`). The parsers,
the comparison key and the usage analysis were ported from the author's BI
Governance Studio, where each rule was earned by a wrong report; `NOTICE` says
which. Qlik, Qlik Cloud and Qlik Sense are trademarks of QlikTech International
AB; this project is not affiliated with or endorsed by Qlik.
