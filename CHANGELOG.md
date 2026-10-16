# Changelog

## 0.3.0 - 2026-10-16

The developer note and the audit log.

- `add_developer_note` and a `developer_note` parameter on `apply_master_items`:
  the dry run proposes the text, the user approves it, the entry goes under
  the script's "change log" / "developer notes" / "developer log" heading
  (with or without hashes), newest first, stamped with date, user and
  "via Ariadne". `SetScript` is sent only through `set_script_inserted`, which
  verifies the new script is the old one plus the inserted lines.
- `ARIADNE_AUDIT_LOG`: append-only JSONL of every write call; a real write is
  refused without it. `read_audit_log` reads it back.
- `list_developer_notes`: the register across readable scripts plus the
  snapshot markers in the archive spaces. The central tenant document was
  decided against as redundant.
- Facts carry the developer-notes section (`FACTS_VERSION` 6).

## 0.2.1 - 2026-10-16

Metadata only, by construction.

- Engine allowlist holds no data-fetching method; `CreateSessionObject` is
  limited to the five metadata listings and refused when a definition carries
  a hypercube or list object.
- REST calls limited to an allowlist of paths.
- INLINE table rows withheld at the engine layer (header and row count kept);
  script-created variable values withheld (name kept).
- Tests pin every refused method, the session-object check, the path
  allowlist and the withholding.

## 0.2.0 - 2026-10-16

A guarded write path, and an assumptions register.

- `ariadne.qlik.policy` under every call: HTTP DELETE never sent, Engine
  allowlist without Destroy methods, writes behind `ARIADNE_ALLOW_WRITE=1`.
- Snapshot before write: a published app's copy is exported without data and
  imported into the archive space the routing rule selects, once per publish,
  stamped with a marker; the write is refused if the snapshot cannot be placed.
  `ARIADNE_ARCHIVE_SPACE_ID`, `ARIADNE_ARCHIVE_MAP`, `ARIADNE_SNAPSHOT_WITH_DATA`.
- New tools: `snapshot_app`, `archive_app`, `apply_master_items` (exact title,
  duplicates in full, near miss refused, no delete mode, dry run by default),
  `compare_app_versions` (what a rollback would undo).
- Items now carry `publish_time` and `origin_app_id`.
- `docs/ASSUMPTIONS.md`: every platform behaviour Ariadne depends on, its
  source, confidence, and how to verify it on the tenant.
- Three new test suites: policy, snapshot, writes; the server suite covers the
  write path against the fake tenant.

## 0.1.0 - 2026-10-16

First iteration: a read-only MCP server for Qlik Cloud governance.

- Thirteen tools in three groups (reach, thread, govern), one prompt, one resource.
- Every result is an envelope with data, coverage, unknowns, limitations,
  evidence and a confidence set from coverage.
- Parsers ported from BI Governance Studio: comments stripped, `$(vVar)` paths
  resolved from literal SETs, run-time names kept as unresolved, JOIN/KEEP and
  loop-loaded tables parsed, a preceding LOAD and its SQL SELECT counted once.
- Permission handling ported: a refused script is a finding with the rule and
  the governed fix; owned apps read first; a systemic refusal stops the sweep
  and says so; the key never reaches a result or a log.
- Credential-looking values in CONNECT strings masked before a script leaves
  the process.
- Five test suites, no framework; a fake tenant exercises every tool.
