# Changelog

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
