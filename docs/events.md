# Events, log lines and websocket API

Reference for what Login Detail reports and how to read it. For installation
and the Sign-in log panel, see the [README](../README.md).

## Events

| Event | Fields |
|---|---|
| `login_detail_failed` | `username`, `user_id`, `ip_address`, `reason`, `flow_id`, `auth_provider`, `endpoint`, `user_agent` |
| `login_detail_succeeded` | `username`, `user_id`, `ip_address`, `flow_id`, `auth_provider`, `endpoint`, `user_agent` |
| `login_detail_request_rejected` | `ip_address`, `endpoint`, `reason`, `rejection`, `user_id`, `username`, `client_id`, `client_name`, `token_type`, `user_agent` |

`username` on a failed attempt is only filled in if it belongs to an existing
user; otherwise it is `null`, so a password typed into the username field is
never recorded. `user_id` is filled in whenever the user is known.

With the option **Also store usernames that do not exist** (0.9.0, off by
default), a failed attempt with a username no user has keeps the name as
typed - cut to 64 characters, control characters replaced by `\ufffd` - and
`login_detail_failed` gets an extra field `known_user: false` (`user_id` is
`null`). The log line quotes such a name and ends in `known_user=False`.
Without the option, or for a user that exists, the field is absent:

```text
Sign-in log: type=auth.login_failed reason=invalid_auth username='admin' user_id=None auth_provider=homeassistant client_id=None token_type=None client_name=None ip=203.0.113.7 endpoint=/auth/login_flow user_agent='…' flow_id=01J… known_user=False
```
`user_agent` is the client's `User-Agent` header, cut to 256 characters.

Every event is also written as one line to the log - failed attempts at
`WARNING`, successful logins and rejected requests at `INFO` (core already
logs rejected requests at `WARNING`):

```text
Sign-in log: type=auth.login_failed reason=invalid_auth username=alice user_id=9f2c… auth_provider=homeassistant client_id=None token_type=None client_name=None ip=192.0.2.10 endpoint=/auth/login_flow user_agent='Mozilla/5.0 …' flow_id=01J…
Sign-in log: type=auth.request_rejected reason=expired_token username=alice user_id=9f2c… auth_provider=None client_id=https://mcp-client.example token_type=normal client_name=None ip=192.0.2.10 endpoint=/api/mcp user_agent='…' flow_id=None
```

`login_detail_request_rejected` covers requests that are rejected for invalid
authentication *outside* the login flow - typically a client with a wrong or
expired token on `/api/mcp`, `/api/websocket` or `/auth/token`. `reason` is
always `invalid_request_auth`; `rejection` says why (see
[Rejected requests](#rejected-requests)). `endpoint` is the route pattern (`/api/mcp`,
`/api/hassio_ingress/{token}/{path}`), never the literal path or query string,
which can carry secrets. Login flow attempts always report `/auth/login_flow`.
Rejections on ordinary views such as `/api/mcp` require IP banning to be
enabled - see [Known limitations](known-limitations.md).
A request to `/auth/login_flow/{flow_id}` that core rejects (for example an
expired flow) is a rejected request, not a failed login.

The `reason` field of `login_detail_failed`:

| `reason` | Meaning |
|---|---|
| `invalid_auth` | Wrong username or password. |
| `invalid_mfa_code` | **The password was correct**; only the 2FA code was wrong. |
| `mfa_too_many_retry` | The password was correct; the flow was aborted after too many wrong 2FA codes. |
| `mfa_login_expired` | The password was correct; the 2FA step expired. |
| `mfa_pending` | The password was correct, and the 2FA code has not been submitted (yet). Replaced by `ok` in the history if the same flow completes. |
| `invalid_request_auth` | A request outside the login flow was rejected for invalid authentication (only on `login_detail_request_rejected` and in the history). |

`invalid_mfa_code` is the one that matters most: someone had the right
password and was stopped only by 2FA. `mfa_pending` that never turns into a
successful login means the same.

## Rejected requests

Core rejects every request with the same 401. Since 0.7.1, Login Detail
works out why from what the request carried, and puts it in `rejection` on
the event and in `reason` in the sign-in log and the log line:

| `rejection` | Meaning | User and client |
|---|---|---|
| `no_credentials` | No token at all - for example an MCP client's first call, which core answers with 401 and `WWW-Authenticate` so the client starts OAuth. | - |
| `expired_token` | A token that really was issued to this user, but has expired. | yes |
| `user_not_allowed` | A valid, unexpired token, but core does not let the user in: the user is set to local access only and connects from outside. | yes |
| `unknown_or_revoked_token` | A token for a refresh token that does not exist (any more) - revoked, logged out, or the user was deactivated - or a forged or garbled token. | - |
| `malformed_credentials` | An `Authorization` header that is not `Bearer <token>`, or a websocket auth message without a usable token. | - |
| `invalid_signed_path` | A signed path (`authSig`) that core did not accept. | - |
| `not_admin` | A valid user, turned away by the endpoint because they are not an administrator - for example the MCP server with "require admin" on. | yes |
| `permission_denied` | The same for an administrator (another permission check). | yes |
| `invalid_auth_request` | `/auth/token` or `/auth/login_flow` rejected the request, for example a refresh token that no longer exists. The request body is not read. | - |

**User and client are only filled in when it is proven.** For a rejected
token, the token's signature is checked against the key of the refresh token
it names - the same check core makes, except for the expiry. Only if it
matches are `user_id`, `username`, `client_id` (for example the OAuth client
ID of an MCP client), `token_type` (`normal`, `long_lived_access_token` or
`system`) and, for long-lived tokens, `client_name` (the name the user gave
the token) filled in. A forged, unsigned or unknown token gets `null` in all
of them, whatever it claims. For `not_admin` and `permission_denied`, core
has already verified the token. `username` comes from the user's stored
credentials, never from the request.

**The token itself is never stored, logged or shown** - not the token, not
its signature, not its payload. Only what is derived from it is kept. The
check is two JWT decodes in memory after core has answered: no I/O, no
network calls, and a failure only means the fields stay `null`.

Rejected requests stored before 0.7.1 show `invalid_request_auth` as their
reason.

## Websocket API

The [Sign-in log panel](../README.md#sign-in-log) only talks to the websocket
command `login_detail/sign_in_log/list`, which core refuses for users who are
not administrators:

```json
{"id": 1, "type": "login_detail/sign_in_log/list",
 "log_type": "auth.login_failed", "ip": "192.0.2.", "limit": 100}
```

All filters are optional: `log_type` (`auth.login_failed`,
`auth.login_success`, `auth.request_rejected`), `reason` (exact, for example
`expired_token`), `username`, `user_id`, `ip`, `start_time`, `end_time`
(ISO 8601) and `limit`. The result is
`{"entries": [...], "total": n, "max_entries": n, "max_days": n}`.

Each entry has `ts`, `type`, `reason`, `username`, `user_id`,
`auth_provider`, `ip`, `hostname`, `endpoint`, `user_agent`, `flow_id`,
`count`, and since 0.7.1 `client_id`, `client_name` and `token_type`. The
last three are only set for a rejected request whose token was proven (see
[Rejected requests](#rejected-requests)); otherwise they are `null`. An entry
whose username no user has (see [Events](#events)) also has
`known_user: false`; no other entry has that key.
