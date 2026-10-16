"""Qlik Cloud access and analysis. No MCP, no GUI: importable and testable on
its own. The parsers in `script` and `expressions` take text and return facts;
`rest`, `engine` and `bulk` talk to a tenant.

Ported from the author's BI Governance Studio (Eunomia), which earned every
rule here the hard way - see each module's docstring for the defect it pins.
"""
