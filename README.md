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
| | `compare_app_versions` | What differs between two apps (a snapshot and its source): script diff, variables, master items, sheet objects. |
| Write | `snapshot_app` | The rollback snapshot of an app's published state: export without data, import into the archive space the routing rule selects, once per publish. Dry run by default. |
| | `archive_app` | Move an app into an archive space. Never a copy, never a delete. Dry run by default. |
| | `apply_master_items` | Create or update master measures and dimensions from definitions. Exact title, duplicates in full, near misses refused, snapshot first, approved developer note after. Dry run by default. |
| | `add_developer_note` | Insert an approved note under the script's developer-notes heading. Insert-only, verified. Dry run by default. |
| Gateway, read | `search` | Apps, datasets, glossaries, automations, data products and more by name, with space and owner. |
| | `list_sheets`, `get_sheet_details` | Sheets, and every object on a sheet by its definition: type, title, dimensions, measures, expressions. Never evaluated. |
| | `get_data_model` | Tables, fields, key types, distinct-value counts and keys. Structure only. |
| | `get_dataset` | A dataset's metadata and schema. Profile and sample rows are refused by policy. |
| | `list_glossaries`, `search_glossary_terms`, `get_glossary_term` | The business glossary. |
| | `list_reloads`, `get_reload`, `get_reload_log` | Reload history, status and the masked log. |
| | `list_automations`, `get_automation` | Automations and their runs. |
| Gateway, write | `start_reload`, `cancel_reload` | Reloads, logged. |
| | `create_glossary_term`, `update_glossary_term` | Terms created as draft, retired by deprecating. Near-duplicate names refused. Never deleted. |
| | `start_automation` | Run an automation, logged. |
| | `create_sheet`, `add_chart` | A sheet, and a chart defined from fields, expressions or master items, placed on the grid. Snapshot first, approved note, logged. Never evaluated. |
| Audit | `list_developer_notes` | The register: every app's developer-notes section across readable scripts, plus the snapshots in the archive spaces. |
| | `read_audit_log` | The last entries of Ariadne's own append-only log of writes. |

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
| `ARIADNE_ALLOW_WRITE` | `1` enables the write tools for this process | off |
| `ARIADNE_ARCHIVE_SPACE_ID` | default archive space for snapshots and `archive_app` | none (content writes refused) |
| `ARIADNE_ARCHIVE_MAP` | `pattern=space_id;pattern=space_id`: a space id, or a glob on the space name, routes to that archive space | none |
| `ARIADNE_SNAPSHOT_WITH_DATA` | app ids or name globs whose snapshots keep their data (apps that hold user-entered data) | none |
| `ARIADNE_AUDIT_LOG` | path of the append-only JSONL audit log; a real write is refused when it cannot be appended to | a per-user file on this machine |
| `ARIADNE_AUDIT` | `writes` (default), `all` (dry runs too) or `off` | writes |

Example for a tenant with two archive spaces:

```bash
export ARIADNE_ALLOW_WRITE=1
export ARIADNE_ARCHIVE_SPACE_ID=6238963d4769f1ced8bf9931            # Archive
export ARIADNE_ARCHIVE_MAP='*finance*=67d04f9b700c02cbea1f67e8'      # Archive - Finance
```

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

## Metadata only, by construction

Ariadne reads no data rows, in any mode, and there is no setting that makes it
do so. The guarantee is in `ariadne.qlik.policy`, under every call:

- The Engine allowlist holds no data-fetching method. `GetHyperCubeData`,
  `GetListObjectData`, `GetFieldValues`, `ExportData`, `SearchValues` and every
  other method that returns values are refused before a socket is touched.
- A session object may only be one of five metadata listings (measures,
  dimensions, variables, fields, sheets), so `GetLayout` can never carry data
  pages.
- REST calls are limited to an allowlist of paths (items, apps, spaces, users,
  lineage graphs, temp contents). No dataset, data file or sample endpoint
  is reachable.
- A load script's `INLINE` rows are withheld at the engine layer; the header
  line and a row count remain. A script-created variable's value is withheld;
  its name remains.
- The snapshot export is a `.qvf` without data that passes from the tenant
  through the local process back into the tenant. It never enters a tool
  result. For apps on the with-data exception list, the same holds.

What still reaches the model is what a developer typed: expressions, variable
definitions, load scripts without their inline rows, file and table names,
descriptions, titles, row counts. A literal inside an expression
(`if(Country='Sweden', ...)`) is business logic, not a record, but it is text
your developers wrote and the reviewer of the policy should know it travels.

## Writes, and what is never done

Writes are off unless `ARIADNE_ALLOW_WRITE=1`, and the key's own rights decide
what can be changed. Three rules sit below every tool, in `ariadne.qlik.policy`,
where no tool or prompt can widen them:

- **Nothing is deleted.** The HTTP method DELETE is never sent. The Engine API
  runs on an allowlist that holds no Destroy method. `apply_master_items` has
  no delete mode. Moving to an archive space is allowed.
- **A published app is snapshotted before it is changed.** If the app has a
  published copy in a managed space, that copy is exported without data and
  imported into the archive space the routing rule selects, named
  `<app> <date> (published <publish date>)` and stamped so the next write after
  the same publish takes no new snapshot. If the snapshot cannot be placed, the
  write does not happen. `compare_app_versions` shows what a rollback would undo.
- **A write never guesses.** Exact titles only; a near miss is refused and
  named; a title carried by several items is updated on all of them and the
  result says so. Every write tool defaults to `dry_run=true` and returns the
  exact actions.

Apps that will hold user-entered data (input and save) go in
`ARIADNE_SNAPSHOT_WITH_DATA`; their snapshots keep the data.

### The developer note and the audit log

Every change through Ariadne leaves two records.

- **A developer note in the load script.** Your scripts carry a `/* ... */`
  block that holds only notes, oldest first, in this form:

  ```
  /*
  - 2026-05-10 - Sebastian H.:
  	- Added export section for Benchmark;
  */
  ```

  Ariadne finds the block by its entries, or by a "change log", "developer
  notes" or "developer log" heading above it (with or without hashes). The
  dry run of a write proposes the note text; the user approves it by passing
  it back, as written or edited, in `developer_note`. After the change is
  saved, the entry is appended at the bottom of the block, before `*/`, in the
  same form, with the user's name and "via Ariadne"; `;` or line breaks in the
  note become bullets. The script sent back is verified to be the old script
  plus those lines and nothing else; any other difference is refused. No block,
  or several, is a refusal too: Ariadne does not invent one, and `block_line`
  chooses when there are several.
- **An audit log on disk.** One JSON line per write call: timestamp, user,
  tool, app, dry run or real, actions, the note, the snapshot ids. Never the
  key, never a script body. There is always a log: unset, it is a per-user
  file on the developer's machine; set `ARIADNE_AUDIT_LOG` to a per-user file
  in a shared folder the BI team owns (a synced SharePoint or OneDrive library
  works) to make the record central. The log is opened for appending before a
  real write is made, and the write is refused if that fails, so nothing
  changes unlogged. `ARIADNE_AUDIT=off` is the only way around it, and it is a
  deliberate setting a reviewer can see in the client configuration. A small
  Qlik app that loads the folder is the usual way to show the record in the
  tenant.

`list_developer_notes` assembles the register from the scripts and the archive
markers, so there is no central document to keep in sync.

## Beside the official Qlik MCP server

Qlik's server (help pages dated 2026-10-15) covers analysis, selections,
chart building, automations, glossary and data products, and returns data.
Where that server cannot be used, Ariadne is the single gateway: it covers the
structural half of that catalogue (sheets, data model, datasets, glossary,
reloads, automations, sheet and chart creation) plus the tenant-wide
governance questions, and leaves out everything that returns data rows or
sets selections.

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
| Writes | Many (apps, scripts, reloads, automations, glossary, data products, bookmarks), including deletes of MCP-created items | Master items (create, update), snapshot, move to archive. No delete anywhere, dry run by default |

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

Version 0.4.0: the single Qlik gateway, metadata only by construction. Forty
tools: reach, lineage, governance, the gateway reads and writes (sheets, data
model, datasets, glossary, reloads, automations), a guarded write path, and
the developer note and audit log behind every change. There is no data mode;
`docs/DESIGN.md` records why and what it would take. `docs/DESIGN.md` holds
the gap analysis against the official server, the write policy, the decisions
taken, the ones still open, and the roadmap. `docs/ASSUMPTIONS.md` lists every
platform behaviour Ariadne depends on and how to verify it on your tenant;
the source application is a starting point, not a reference.

## License and provenance

Copyright Sebastian Hansson, Apache License 2.0 (see `LICENSE`). The parsers,
the comparison key and the usage analysis were ported from the author's BI
Governance Studio, where each rule was earned by a wrong report; `NOTICE` says
which. Qlik, Qlik Cloud and Qlik Sense are trademarks of QlikTech International
AB; this project is not affiliated with or endorsed by Qlik.
