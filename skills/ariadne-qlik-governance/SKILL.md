---
name: ariadne-qlik-governance
description: >-
  Run a Qlik Cloud governance review with the Ariadne MCP server: ownership
  continuity, extractors and QVD lineage, master-item consistency across apps,
  unused fields in an app. Use when the user asks who owns what, where a QVD
  comes from, who reads a QVD, whether apps define a measure consistently, what
  an app's load script does, or for a tenant health or governance review. Do
  not use for data questions, selections, chart building or writes; those
  belong to the official Qlik MCP server.
license: Apache-2.0
metadata:
  author: sebastianhanssonpriv
  version: 0.1.0
  tags:
    - qlik
    - mcp
    - governance
    - lineage
allowed-tools: tenant_overview list_apps describe_app diagnose_app_access get_load_script analyze_load_script get_lineage_graph trace_qvd list_extractors get_master_items compare_master_items find_unused_in_app check_ownership
---

# Ariadne Qlik governance

Ariadne is read-only. Every tool returns an envelope: `data`, `coverage`,
`unknowns`, `limitations`, `evidence`, `confidence`, `framing`.

## Rules you must follow

1. Read `coverage` and `unknowns` before stating any conclusion. Report the
   coverage numbers with the finding ("over 1,611 of 1,908 readable scripts").
2. A `null` value means the tenant did not report it. Never read it as "no".
   `published: null` is "publish state not reported", not "unpublished".
3. `access.state: "refused"` is a finding about permissions. Quote the `rule`
   and the `fix`. It says nothing about the object.
4. An app in `unread_apps` or `possible_runtime_references` may read or write
   the QVD. Say so; do not drop it.
5. Present results as a prioritised worklist to verify, in the user's words,
   and keep `limitations` with them.

## Workflow

1. `tenant_overview` first. State what the key can see and the blind spots.
2. For an ownership review: `check_ownership`, then `describe_app` on the top
   rows for context.
3. For lineage: `analyze_load_script` on the app, then `trace_qvd` for each QVD
   it reads, then `get_lineage_graph` to cross-check with Qlik's own view.
   Where the two disagree, say which is which; neither is complete.
4. For consistency: `list_apps` with `published=true` in the space, then
   `compare_master_items` on up to 25 app ids.
5. For cleanup candidates: `find_unused_in_app`. Always list the dynamic
   expressions it reports before recommending a deletion.

Tenant-wide tools take minutes on large tenants. Narrow with `space_id` or
`max_apps` when the question allows, and say that you did.
