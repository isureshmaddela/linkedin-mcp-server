"""API: a local SerpApi console, SerpApi-compatible endpoint and Careers engine layer.

The package keeps the same split as the rest of ``aira``: pure domain modules
(``catalog``, ``client``, ``store``, ``service``, ``compat``) that never import
the HTTP server, plus ``http`` and ``routes`` which describe the HTTP surface in
a framework-neutral way. ``auto_apply_assistant.py`` installs the routes.
Design contract: ``docs/airaapi-spec.md``.
"""
