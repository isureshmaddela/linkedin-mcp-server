"""Aira Career Copilot domain modules.

These modules hold provider-neutral domain logic (scoring, field mapping, ATS
adapters, email intelligence, documents, calendar, analytics, schema
migrations). They never import the HTTP server; the server module
``auto_apply_assistant.py`` wires them together and passes SQLite connections
explicitly so each module stays testable in isolation.
"""

__all__ = ["__version__"]
__version__ = "1.0.0"
