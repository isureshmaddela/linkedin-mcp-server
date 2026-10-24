"""Aira Career Copilot, hosted multi-tenant shell.

The domain code in ``aira/`` is shared with the local single-user build. This
package adds what a hosted service needs around it: organizations as tenants,
members and roles, plans and entitlements, billing, an organization dashboard,
a platform console, a tenant-fair job queue with workers, and the HTTP gateway
that binds every request to one organization before any domain code runs.
"""

__version__ = "1.0.0"
