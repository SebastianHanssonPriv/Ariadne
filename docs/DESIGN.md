# Ariadne design: a governance MCP beside Qlik's own

Audience: the owner of this repository (technical), and whoever decides
whether a BI governance team should run it (business). Technical detail is
kept to the later sections.

## 1. Context

Qlik ships an MCP server with every Qlik Cloud subscription. The facts below
are taken from Qlik's help pages as saved on 2026-10-15 (Connecting, Deploying
and administering, Access and permissions, Tools, Skills and plugins) and
from the `qlik-oss/agentic-skills` repository.

**Facts (documented by Qlik):**

- Remote server at `https://<tenant-id>.<region>.qlikcloud.com/api/ai/mcp`.
  Must be activated by a tenant administrator, who makes the first connection
  for each LLM client identity.
- OAuth 2.0 only: CIMD (recommended for Claude), DCR, or a manually created
  client with scopes `user_default` and `mcp:execute`, optionally
  `offline_access` (re-authentication every 30 days instead of more often).
  Orchestration frameworks need a web client with machine-to-machine enabled.
- Users need a custom role with `Qlik MCP: Allowed` under Features and
  actions, Agentic AI. Tools act as the user: space roles and Section Access
  apply.
- Billing: every five billable tool calls consume one question from the
  monthly quota, shared with Qlik Answers. Guardrails: 200 concurrent app
  sessions per tenant, 300 tool calls per user per minute.
- Catalogue: about 95 tools in 14 categories. Relevant to governance:
  `qlik_search`, `qlik_describe_app`, `qlik_get_fields`, `qlik_get_script`,
  `qlik_update_script`, `qlik_get_data_model`, `qlik_get_lineage` (one step
  upstream per call, "call recursively for full chain"), `qlik_list_measures`,
  `qlik_list_dimensions` (update and delete only for items created through
  MCP), the glossary, dataset quality and data product tools.
- Skills: Qlik maintains an Agent Skills repository with official and
  community skills. The official `qlik-ai-readiness-optimizer` skill states
  that "absence of readable data isn't evidence of absence" and refuses to
  score a layer it cannot read.

**Interpretations (reasoned from the catalogue, not stated by Qlik):**

- Every official tool is scoped to one object. There is no tenant-wide
  listing, comparison or aggregation; the model would have to loop, and each
  iteration is a billable call.
- `qlik_get_script` returns text. Turning text into facts (what is stored,
  what is read, which names are built at run time) is left to the model,
  which has no comment stripping, no variable resolution and no way to say
  "unresolved" reliably.
- Lineage is Qlik's own graph, one hop at a time. A producer-to-consumer map
  over QVDs across the tenant is not offered.
- Nothing in the catalogue addresses ownership continuity, unused content, or
  cross-app consistency of master items.
- Coverage and uncertainty are not part of the result contract. Whether a
  tool answered for all of a scope or part of it is not reported in a
  machine-readable way.

## 2. The problem Ariadne solves

A governance team's questions are tenant-wide and evidence-weighted: "which
apps still depend on `Orders.qvd`", "whose apps have no owner any more",
"do Finance and Sales mean the same by Net Sales". Asked through the official
server they are either not answerable, or answerable only by a model looping
over a thousand billable calls and then summarising text it cannot parse
reliably. The failure mode is not an error but a confident wrong answer:
"nothing reads this QVD" when 290 scripts were simply refused.

## 3. Proposed design (what was built in 0.1)

**Position.** Complement, not replacement. The official server does;
Ariadne says how far the doing can be trusted. Both can be connected to the
same client, and the shipped skill tells the agent which to use for what.

**Components.**

| Component | Role |
|---|---|
| `ariadne.server` | The MCP surface. Thirteen read-only tools, one prompt, one resource. No analysis. Injectable backend. |
| `ariadne.envelope` | The result contract: data, coverage, unknowns, limitations, evidence, confidence from coverage, framing. |
| `ariadne.qlik.rest` | Items, Apps, Spaces, Users, lineage-graphs. Gzip, Retry-After, no retry on 404. |
| `ariadne.qlik.engine` | Engine API over WebSocket: script, master items, fields, tables, every object's full property tree. |
| `ariadne.qlik.script` | Load-script facts. Comments stripped, `$(vVar)` resolved from literal SETs, unresolved names kept apart, JOIN/KEEP/loops parsed, secrets masked. |
| `ariadne.qlik.expressions` | Field references and the comparison key; cross-app consistency; usage in an app. |
| `ariadne.qlik.ownership` | Owner state classification and priority without penalising gaps. |
| `ariadne.qlik.bulk` | Concurrent script reads, owned-first ordering, systemic-refusal stop, facts cache keyed on reload time. |

**Data flow.** Client calls a tool over stdio. The tool fetches through the
backend (REST or Engine), hands raw payloads to pure functions, and wraps the
result in an envelope. Tenant-wide tools read every script once per session
per reload time and keep only derived facts (27 times smaller than the
scripts, and free of connection strings).

**Security boundaries.**

- Metadata only, by construction: no Engine method that returns values is on
  the allowlist, a session object may only be a metadata listing, REST calls
  are limited to listed paths, INLINE rows and script-created variable
  values are withheld at the engine layer. There is no data mode.
- One API key, read from the environment, stripped on entry, scrubbed from
  every error and detail before it enters a result. Never logged.
- Read-only: every tool is annotated `read_only_hint`, and the Engine
  session never calls a mutating method.
- Scripts leave the process with credential-looking values masked.
- The server acts as the key's user; Qlik's space roles and Section Access
  apply on the tenant side exactly as for any API client.
- No prompt text, no tool result and no script is stored on disk.

## 4. Improvements over the official server, ranked

| # | Improvement | Why it matters | Status |
|---|---|---|---|
| 1 | Coverage and uncertainty in every result | Prevents a model turning "refused" or "not reported" into "no". The single most common error the source application made. | Built |
| 2 | Tenant-wide script facts with a producer/consumer map | Impact analysis before a QVD is changed; the question the one-hop lineage tool cannot answer. | Built |
| 3 | Permission as a finding, with the rule and the fix | A tenant-admin key can open every app and be refused every script. Saying so, once, with the governed fix, beats 1,900 identical errors. | Built |
| 4 | Behaviour-based extractor classification with evidence | Naming conventions fail on real tenants. | Built |
| 5 | Cross-app master-item consistency on a quote-aware key | The official server lists items per app; consistency is a cross-app question. | Built |
| 6 | Ownership continuity | Not in the official catalogue; no screen in Qlik lists it. | Built |
| 7 | Unused fields and master items with dynamic-expression warnings | Official tools can delete only MCP-created items and have no usage analysis. | Built |
| 8 | Local, unmetered, headless-friendly | No question quota; runs from a scheduled task with a service key. | Built |
| 9 | QVD field-level impact (which fields of a QVD land in which model) | The next question after "who reads it". The source application has this; not yet ported. | Planned 0.2 |
| 10 | Orphan QVDs via the data-files inventory | Files nobody reads or writes, with coverage. Needs the DataFiles connection walk. | Planned 0.2 |
| 11 | Per-user identity on the HTTP transport (OAuth, or Qlik's M2M web client) | Matches the official server's identity model for shared deployments. | Planned 0.3 |
| 12 | Write tools (apply master items from a definition), dry-run by default | Closes the loop from finding to fix; needs the exact-title and duplicate rules from CLAUDE.md. | Planned 0.4 |

## 5. Alternatives considered

- **Skills only, on top of the official server.** Cheapest; no new software.
  Rejected as the whole answer: a skill cannot give the model a parser, a
  cache or a coverage count, and every loop iteration is billable. Kept as a
  complement: Ariadne ships a skill in the same format.
- **A Qlik Automation that produces the reports.** Governed and scheduled,
  but not conversational, and automations cannot read load scripts across
  the tenant with the same depth.
- **Use the official server as Ariadne's backend through an M2M OAuth
  client.** Attractive for identity alignment; rejected for 0.1 because the
  official tools return text, not the raw payloads the parsers need, and
  each call is billable. Revisit for the identity layer in 0.3.
- **Port the whole BI Governance Studio into the server.** Too much surface
  for a first iteration; the desktop application remains the place for
  Excel workbooks and dashboards. Ariadne takes the logic, not the views.

## 6. Risks and trade-offs

| Risk | Mitigation |
|---|---|
| A tenant-wide tool takes minutes and the client times out | `space_id` and `max_apps` narrow the scope; the facts cache makes the second question cheap; the skill tells the agent to narrow. A progress channel is a 0.2 item. |
| The key's user cannot read most scripts | Reported as coverage and as an access finding with the fix (space role "Can edit data in applications"). Never read as "nothing stored". |
| A model ignores the envelope and overstates | The framing sentence is in every result, the skill repeats the rules, and confidence is a field, not prose. Residual risk: the client's own summarisation. |
| Text parsing misses a construct (a new JOIN form, a `$(Include)`) | Unresolved names and include files are reported as limitations; `FACTS_VERSION` invalidates the cache when a parser changes; each parser defect becomes a pinned test. |
| Secrets in scripts | Masked before leaving the process. Residual risk: a credential in a form the regex does not recognise. |
| One key, one identity on HTTP | Documented; stdio is the recommended transport until 0.3. |

## 7. Governance and operations

- **Owner:** the BI governance function that owns the API key. The key
  should belong to a service user with the space roles the scans need, not
  to a person.
- **Deployment:** a Python package on the analyst's machine (stdio), or one
  process per tenant behind the team's own authentication (HTTP).
- **Operation:** no state on disk; restart loses only the in-memory cache.
- **Change management:** a parser change bumps `FACTS_VERSION` and adds a
  test; a new tool adds a `test_server` case against the fake tenant.
- **Licensing:** Apache 2.0 for Ariadne; MIT and Apache 2.0 dependencies.
  Decision pending: whether to align with the source application's Commons
  Clause.

## 8. What can be decided now, and what cannot

**Decide now.** Position (complement), transport for 0.1 (stdio), identity
(one API key, service user), scope (read-only, the thirteen tools), the
result contract (the envelope).

**Cannot be decided yet.** Whether the HTTP transport needs per-user
identity before anyone but the owner runs it; whether write tools are wanted
at all; whether the official server's M2M client is a viable backend.

**Information that would improve the decision.** A run of
`diagnose_app_access` and `tenant_overview` against the real tenant with the
intended service key (how many scripts are readable), and one `trace_qvd`
timing on the full estate (minutes, not an estimate).

## 9. Write policy (0.2)

The tenant is used by a few skilled developers, so write rights stay bound
to the key's user. Ariadne adds only what a key cannot enforce for itself:

| Rule | Where it is enforced | Why there |
|---|---|---|
| Nothing is deleted | `policy.check_http` refuses DELETE; `policy.check_engine` allows only listed methods, none of them Destroy* | Under every call, so no tool, prompt or future code can delete |
| A published app is snapshotted before it is changed | `snapshot.plan` and `snapshot.execute`, called by every content-write tool before the write | The snapshot is the rollback unit: script, variables, master items and visuals in one app |
| Snapshot without data | Export with `NoData=true`, import into the archive space | Data comes back from the pipelines on reload; a copy with data costs capacity and adds nothing. Apps that will hold user-entered data are the exception list |
| Once per publish | Marker in the snapshot's description keyed on source, copy and `publishTime` | "Every change since last published", independent of who made the change |
| Routing by rule | `snapshot.route`: space id or name glob to archive space; default; refused when two rules match or the caller names a non-archive space | A write never guesses; the dry run shows the chosen space, and a refusal names the options so the model can ask |
| Dry run by default | Every write tool | The exact actions are visible before anything happens |

Rollback is a decision, so Ariadne shows what it would undo
(`compare_app_versions`) rather than doing it. A `restore_snapshot` tool is
a later item; until then rollback is republishing the snapshot, or copying it
back as the new source, by a person.

Every change leaves a developer note in the script (approved by the user,
inserted under the existing heading, verified insert-only) and a line in a
local append-only audit log; a real write is refused without the log. A
central register document on the tenant was considered and rejected: Qlik
has no append-only document, a file register races between developers, and
everything it would hold exists in the notes and the log. The register is a
read tool instead (`list_developer_notes`).

Retention: Ariadne never deletes, so archive copies accumulate. A listing of
snapshots older than N days for a person to remove is a 0.3 item.

Assumptions A3 to A7 in `docs/ASSUMPTIONS.md` carry this section; verify them
on one test app with `snapshot_app` and `dry_run=false` before enabling writes
for developers.

## 10. Roadmap

- **0.2** QVD field-level impact; orphan QVDs from the data-files inventory;
  progress reporting for long scans; `trace_qvd` across several names at once.
- **0.3** Streamable HTTP with per-user identity (Qlik OAuth client, M2M);
  audit log of tool calls (who asked what, never the key).
- **0.3 (writes)** `restore_snapshot` (republish or copy back, dry run first);
  snapshot retention listing; a probe tool that verifies A3 to A7 on one app;
  on-disk export of the .qvf as an optional second line of defence.
- **Later** Power BI counterpart (the source application has the analysis),
  a Qlik Automation that calls Ariadne on a schedule and files the worklist.
