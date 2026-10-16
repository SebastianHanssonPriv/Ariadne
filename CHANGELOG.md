# Changelog

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
