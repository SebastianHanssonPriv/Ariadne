"""ariadne.qlik is analysis and tenant access only: no MCP, no GUI. The source
application once became unimportable wherever an SDK was incomplete because a
parsing module pulled in an auth stack."""
import pathlib, subprocess, sys
src = pathlib.Path(__file__).resolve().parent.parent / "src"
code = (f"import sys; sys.path.insert(0, {str(src)!r}); import ariadne.qlik.script, ariadne.qlik.expressions, ariadne.qlik.ownership, "
        "ariadne.qlik.rest, ariadne.qlik.engine, ariadne.qlik.bulk, ariadne.envelope, ariadne.config; "
        "bad=[m for m in sys.modules if m.split('.')[0] in ('mcp','PySide6','PyQt5','tkinter','starlette','pydantic')]; "
        "print(sorted(bad)); sys.exit(1 if bad else 0)")
p = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
print("  modules that must not load:", p.stdout.strip())
print(f"  {'ok  ' if p.returncode == 0 else 'FAIL'} ariadne.qlik loads without mcp, pydantic or any GUI toolkit")
if p.stderr.strip():
    print(p.stderr)
sys.exit(p.returncode)
