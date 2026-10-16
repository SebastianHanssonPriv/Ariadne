# Ariadne - developer notes

Read this before changing anything analytical. It is the short list of rules
this code is built on. Each exists because breaking it, in the application
these parsers were ported from, produced a wrong report.

```bash
python tests/run_all.py
```

Five suites, no framework. Run them after touching a parser, a classifier, a
score or a tool. `tests/run_all.py` says what each protects.

## 1. Absence of evidence is never evidence

`None` means "not known" and must survive into the result as its own state:
`published: null`, `loads_external: null`, `owner.state: "unverified"`,
`is_extractor: null`. Never `bool(x.get("published"))` on a field the tenant
may not report. A refused call is `access.state: "refused"` with `data: null`,
the rule and the fix: a finding about the key, not a fact about the object.
A gap never becomes a penalty: an unreported input scores 0 in `ownership.priority`.

## 2. Say what you did not measure

Every tool returns the envelope (`envelope.py`). Anything partial reports
`coverage` as counts in the result, not only in a log. `confidence` is derived
from coverage by `confidence_from`; never set it from wording.

## 3. Classify on behaviour, not on names

An extractor is an app whose script loads from outside Qlik and stores a QVD
(`script.extractor_reason`). It is not an app with "extractor" in its title;
the real tenant's extractors are called "QVD creator".

## 4. Comments are not code, and strings are not code

- Load scripts: `script.strip_script_comments` (keeps the `//` in `lib://`).
- Expressions: `expressions.split_expression` (drops comments and literals,
  keeps literals when the expression contains `$(`).
- Comparison keys: `expressions.tight` (whitespace outside literals only).

Resolve `$(vVar)` before matching filenames. A name still holding `$(` after
resolution is **unresolved** and reported as such, never invented.

**Bump `script.FACTS_VERSION` when you change a parser**; cached facts are
keyed on it.

## 5. Permission is a finding, not an error

Opening an app and reading its script are two different rights. The script is
given up to the app's owner, or to a user holding "Can edit data in
applications" in the app's shared space; Tenant Admin and Developer do not
grant it. `bulk.owned_first` only sorts the work; no app is skipped on a guess.
The systemic abort fires only when the first thirty reads all failed the same
permanent way and none succeeded.

## 6. Cost, and what it buys

Everything REST goes through `rest.request_json`: gzip, Retry-After on 429 and
503, no retry on a 404 (most apps have no lineage graph). Tenant-wide reads run
`ARIADNE_WORKERS` sessions, modest on purpose: the limit is the tenant's. The
lineage graph is fetched uncollapsed because collapsed drops QVD nodes.

## 7. Layers

`ariadne.qlik.*` and `ariadne.envelope` hold the logic and import no MCP and
no GUI (`tests/test_layers.py`). `ariadne.server` holds no analysis: it fetches,
calls the pure functions, and wraps. The backend is injectable so every tool
runs against a fake tenant in `tests/test_server.py`.

## 8. The key never leaves the process

`config.scrub` on every error and detail string that goes into a result.
`script.redact_secrets` on every script that goes out. No logging of requests.

## 9. Reports are worklists

Every envelope carries `framing`. Keep it. A tool people trust to be certain is
more dangerous than one they trust to be useful.

## 10. Writes: never delete, snapshot first, never guess

`qlik/policy.py` sits under every REST and Engine call: DELETE is never sent,
the Engine allowlist holds no Destroy method, and write methods need
`ARIADNE_ALLOW_WRITE=1`. Add a method to the allowlist only with a test in
`test_policy.py`. A write to an app with a published copy runs
`snapshot.plan` and `snapshot.execute` first and does not proceed if the
snapshot cannot be placed. `writes.apply_master` matches on the exact title,
acts on all duplicates and says so, refuses near misses, and dry-runs by
default. Routing to an archive space is by rule and refused when ambiguous.

## 12. Eunomia is a starting point, not the truth

The source application's rules were earned on one tenant and carry their own
assumptions. Every platform behaviour Ariadne depends on is listed in
`docs/ASSUMPTIONS.md` with how to verify it. When you port a rule, add its
assumption there; when a tenant contradicts one, fix the code and the entry.

## 11. Style

No em dash anywhere in code, docs or output: a hyphen or a new sentence.
Preserve exact object names (tools, fields, APIs).
