"""Run every suite under tests/ and report.

    python tests/run_all.py            every suite
    python tests/run_all.py script     only suites whose name contains "script"
    python tests/run_all.py -v         show each suite's own output

No test framework and no dependency: a suite is an ordinary script that
asserts, prints what it checked, and exits non-zero when something is wrong.
Each one pins a defect that was real in the application this was ported from,
and the printed output says what the defect was. Exit 2 is a warning: something
actionable that no commit can fix.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
COVERS = {
    "test_script": "load-script facts: comments, $(vVar) paths, STORE forms, JOIN/KEEP, extractors",
    "test_expressions": "what counts as a field reference; when two calculations are the same",
    "test_envelope": "the envelope: tri-state fields, coverage-driven confidence, refusals",
    "test_server": "every tool against a fake tenant: read-only, refusals as findings, key never leaks",
    "test_layers": "ariadne.qlik imports no MCP and no GUI",
    "test_policy": "DELETE never sent, no Destroy method on the allowlist, writes behind the flag",
    "test_snapshot": "snapshot before write: routing by rule, once per publish, without data, refused when ambiguous",
    "test_writes": "apply master items: exact title, duplicates in full, near miss refused, no delete mode",
}


def main(argv):
    verbose = "-v" in argv
    pats = [a for a in argv if not a.startswith("-")]
    suites = sorted(p for p in HERE.glob("test_*.py"))
    if pats:
        suites = [s for s in suites if any(p in s.stem for p in pats)]
    results = []
    for s in suites:
        t0 = time.time()
        proc = subprocess.run([sys.executable, "-I", str(s)], capture_output=True, text=True)
        dt = time.time() - t0
        tag = "ok  " if proc.returncode == 0 else "warn" if proc.returncode == 2 else "FAIL"
        results.append((s.stem, proc.returncode))
        print(f"{tag}  {s.stem:20} {dt:5.1f}s  {COVERS.get(s.stem, '')}")
        if verbose or proc.returncode not in (0,):
            for line in (proc.stdout + proc.stderr).rstrip().splitlines():
                print("      " + line)
    failed = [n for n, rc in results if rc not in (0, 2)]
    print()
    print(f"{len(results) - len(failed)} of {len(results)} suites pass"
          + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
