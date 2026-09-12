"""Sibling module shipped next to the notebook, used to test multi-file imports."""

SCHEMA_BASE = "https://example.org/"


def qualify(term: str) -> str:
    return term if ":" in term else SCHEMA_BASE + term


class HelperMarker:
    origin = "oold_helpers.py"
