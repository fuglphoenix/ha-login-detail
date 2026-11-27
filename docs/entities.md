# Entities

Six sensors are created automatically. No YAML, no helpers.

**Every user of Home Assistant can read every sensor and its history -
administrators and other users alike.** That is why, since 0.12.0, the
sensors show no usernames, IP addresses, hostnames or session users unless
you turn on **Show usernames and IP addresses in the sensors**
(`sensor_details`, off by default). The admin-only
[Sign-in log](../README.md#sign-in-log) panel shows all of it either way, and
the [events](events.md#events) carry it for automations. A username no user
has is never in a sensor, not even with the option on.

| Entity ID (new installation) | Entity ID (installed before 0.8.0) | Shows | Added with `sensor_details` |
|---|---|---|---|
| `sensor.login_latest_event` | `sensor.login_seneste_haendelse` | Latest login event as `result · endpoint`. Attributes: `time`, `result`, `auth_provider`, `endpoint`, `count`. | State `result · username · ip` (the endpoint instead of the username for a rejected request); attributes `username`, `ip`, `hostname`. |
| `sensor.login_ok_24h` | `sensor.login_ok_24t` | Number of successful logins in the last 24 hours, with per-endpoint counts (`endpoints`) and `latest`. | Per-IP (`ip_addresses`) and per-username (`usernames`) counts. |
| `sensor.login_failed_24h` | `sensor.login_mislykkede_24t` | Number of failed login attempts and rejected requests in the last 24 hours, with per-endpoint counts (`endpoints`) and `latest`. | Per-IP (`ip_addresses`) and per-username (`usernames`) counts. |
| `sensor.login_ip_addresses_24h` | `sensor.login_ip_adresser_24t` | Number of unique IP addresses that tried to log in or were rejected in the last 24 hours, and `banned`: how many of them are on core's IP ban list now (0.12.0). | `addresses`: hostname, counts, usernames, endpoints and `banned` (0.11.0) per IP, for the 20 addresses with the most attempts, at most 10 usernames and endpoints each; `addresses_not_shown` counts the rest (0.12.0). |
| `sensor.login_history` | `sensor.login_historik` | Number of stored events (up to the `max_entries` option, default 500); the latest 25 are in the attributes, with `time`, `result`, `auth_provider`, `endpoint` and `count`. The entry of the attempt that got its IP address banned also has `ip_banned_now`, `attempts` and `threshold` (0.11.0). | `username`, `ip`, `hostname` per event. |
| `sensor.login_active_sessions` | `sensor.login_aktive_sessioner` | Number of active refresh tokens, each with its `type` and when it was `last_used`. System tokens are not counted. | `user`, `ip` and `client` per session, and per-IP counts (`ip_addresses`). |

The recorder keeps what a sensor showed for as long as it keeps history
(10 days by default). Turning the option off stops new states from carrying
the details; to remove the old ones, call `recorder.purge_entities` for the
six sensors.

Which of the two entity IDs you get, and the attribute keys before and since
0.8.0, are described in [Upgrading to 0.8.0](upgrading.md#upgrading-to-080).

Each history entry gets a hostname from a reverse DNS lookup. The lookup runs
in a background task, so a slow or missing PTR record never delays a login.

Repeated rejections from the same IP address on the same endpoint within 10
minutes are folded into one history entry with a `count`, so a client retrying
with a stale token cannot push real login attempts out of the history. The
24-hour counters count every attempt. The attempt that got an IP address
banned is never folded, and nothing after it is folded into an entry before
it (0.11.0, see [IP bans](events.md#ip-bans-0110)).

The sensors only write a new state when the content actually changes, so no
`recorder` exclusion is needed.
