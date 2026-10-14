# Entities

Six sensors are created automatically. No YAML, no helpers.

| Entity ID (new installation) | Entity ID (installed before 0.8.0) | Shows |
|---|---|---|
| `sensor.login_latest_event` | `sensor.login_seneste_haendelse` | Latest login event as `result · username · ip` (the endpoint instead of the username for a rejected request). Attributes: `time`, `result`, `username`, `ip`, `hostname`, `auth_provider`, `endpoint`, `count`. |
| `sensor.login_ok_24h` | `sensor.login_ok_24t` | Number of successful logins in the last 24 hours, with per-IP, per-username and per-endpoint counts. |
| `sensor.login_failed_24h` | `sensor.login_mislykkede_24t` | Number of failed login attempts and rejected requests in the last 24 hours, with per-IP, per-username and per-endpoint (`endpoints`) counts. |
| `sensor.login_ip_addresses_24h` | `sensor.login_ip_adresser_24t` | Number of unique IP addresses that tried to log in or were rejected in the last 24 hours, with hostname, counts, usernames and endpoints per IP. |
| `sensor.login_history` | `sensor.login_historik` | Number of stored events (up to the `max_entries` option, default 500); the latest 25 are in the attributes. |
| `sensor.login_active_sessions` | `sensor.login_aktive_sessioner` | Active refresh tokens per user, with the IP address and time they were last used. System tokens are not counted. |

Which of the two entity IDs you get, and the attribute keys before and since
0.8.0, are described in [Upgrading to 0.8.0](upgrading.md#upgrading-to-080).

Each history entry gets a hostname from a reverse DNS lookup. The lookup runs
in a background task, so a slow or missing PTR record never delays a login.

Repeated rejections from the same IP address on the same endpoint within 10
minutes are folded into one history entry with a `count`, so a client retrying
with a stale token cannot push real login attempts out of the history. The
24-hour counters count every attempt.

The sensors only write a new state when the content actually changes, so no
`recorder` exclusion is needed.
