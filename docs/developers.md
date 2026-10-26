# Connecting other apps to Aira (the developer platform)

Hosted Aira offers three ways for other software to work with an organization's data. All of them are in
**Developers** (the API app) and run on `aira_cloud/oauth.py`, `aira_cloud/webhooks.py` and
`aira_cloud/routes/developers.py`.

| Way | For | How it proves who it is |
| --- | --- | --- |
| Personal access token | your own scripts, spreadsheets, Zapier/Make-style tools | `Authorization: Bearer aira_pat_...` |
| OAuth 2.0 app | software other people sign in to | authorization code + PKCE, then `Bearer aira_at_...` |
| OAuth client credentials | a server acting for the organization | `client_id` + `client_secret` -> `Bearer aira_at_...` |
| Webhooks | hearing about changes as they happen | Aira signs each post (`Aira-Signature`) |

## Scopes

Every app has `<app>:read` (GET routes) and `<app>:write` (everything else; includes read). `hub:read` / `hub:write`
cover the Business hub, `profile` covers `/api/oauth/userinfo`. A token reaches only the routes of apps it has a
scope for; sign-in, account, organization and platform routes never accept a token (`403 token_not_allowed`), a
missing scope is `403 insufficient_scope`. Client-credentials tokens cannot have personal scopes (Careers and the
everyday apps) because they act for the business, not a person. `GET /api/developers/openapi.json` lists every route
a token can call with the scope it needs (OpenAPI 3.1, works with Postman and code generators).

## OAuth 2.0

* Metadata: `GET /.well-known/oauth-authorization-server` (RFC 8414).
* `GET /oauth/authorize?response_type=code&client_id&redirect_uri&scope&state&code_challenge&code_challenge_method=S256`
  shows the person the app's name, its organization and each permission in plain words; Allow returns
  `redirect_uri?code=...&state=...`, Deny returns `error=access_denied`.
* `POST /oauth/token` (form or JSON; client authentication by HTTP Basic or `client_id`/`client_secret`):
  `authorization_code` (with `code_verifier`), `refresh_token`, `client_credentials`. Responses carry
  `Cache-Control: no-store`.
* `POST /oauth/revoke` (RFC 7009) and `POST /oauth/introspect` (RFC 7662, confidential apps, their own tokens).

Security rules: redirect addresses must match exactly and be https (or http on the loopback for apps on a
computer); PKCE S256 is required for public apps; codes are single use and last ten minutes, and a code presented
twice revokes the tokens it produced; access tokens last an hour; refresh tokens last thirty days and rotate on every
use, and an old refresh token presented again revokes the whole family; tokens and secrets are stored only as
SHA-256 hashes and shown once; deleting an app or removing a member ends their tokens; every grant, consent and
revocation is in the organization's audit log. Apps work for the organization that registered them.

## Webhooks

Admins add an https endpoint and choose events (`*`, `shop.*`, or exact ones). Events come from the apps' activity
(`<app>.<kind>`, for example `shop.order`, `bookings.booked`, `service.case`); personal apps never send events.
Each delivery is a JSON POST:

```
Aira-Event: shop.order
Aira-Delivery: 7c0e...
Aira-Signature: t=1792900000,v1=<hex HMAC-SHA256 of "<t>.<raw body>" with the endpoint's secret>
{"id": "7c0e...", "type": "shop.order", "created": 1792900000, "organization": "...", "data": {...}}
```

Check the signature and that `t` is recent; answer 2xx quickly. Failed deliveries are retried after 1, 5 and 30
minutes, then marked failed; the last 100 deliveries are listed per endpoint. Aira resolves the address and posts
only to public addresses, connects to the address it checked and does not follow redirects.
