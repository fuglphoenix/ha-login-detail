# Testing login_detail

There are two levels of tests.

| Level | What | Where it runs |
|---|---|---|
| Static checks | Manifest, `hacs.json`, translation parity, syntax, no Danish text outside `translations/da.json` | `pytest` - anywhere, also in CI |
| Acceptance test | The whole login path against a real Home Assistant, including 2FA | Inside a Home Assistant container, by hand |

The static checks cannot tell you whether the monkeypatch still works. Only
the acceptance test can. **Run it after every Home Assistant upgrade.**

## Static checks

```sh
pip install pytest ruff
pytest
ruff check custom_components tools tests
ruff format --check custom_components tools tests
```

## Acceptance test

The acceptance test starts **a real, complete Home Assistant** in a throwaway
config inside the core container, with `login_detail` and a test probe next to
it. Real bootstrap, real config entry machinery, real entity registry, real
`AuthManager`, real TOTP - without touching your running instance.

### The probes

| Probe | Exercises |
|---|---|
| `login_detail_probe` | Import from YAML, adoption of existing entities, login with and without 2FA, reload, removal, UI setup, single-instance guard. |
| `login_detail_probe2` | The state after migration: a config entry **without** `login_detail:` in `configuration.yaml`. |
| `login_detail_probe3` | The sign-in log (0.7.0) over real HTTP and websocket: `user_id`, `user_agent`, the username guard, one log line per event, the websocket command and its filters, **refusal for non-admin users**, the panel, retention and the options. |
| `login_detail_probe4` | Rejected requests (0.7.1) on `/api/mcp` (MCP server, "require admin"), the websocket, `/auth/token` and a signed path: every `rejection` type, the user and client of an expired normal and long-lived token, **forged, unsigned, unknown and revoked tokens naming nobody**, a local-only user from outside, `not_admin`, folding, the `reason` filter, **no token or part of one anywhere** (history, storage file, events, websocket, the whole log), the IP ban counter, and that a call without a token can get the IP banned. |
| `login_detail_probe6` | `/auth/token` details and ingress (0.10.0) over real HTTP and websocket: a revoked refresh token, a real one with the wrong client ID, a local-only user from outside, an invalid client ID, no refresh token, a made-up authorization code and an unknown grant type; an expired long-lived token on the websocket; tokens of a deleted refresh token and a deleted user; a random bearer value without an exception; Host and the raw X-Forwarded-For chain through a proxy, marked untrusted, also on a failed login; the websocket command, the log line and the panel; **the self-check and its fallback**; and **no token, refresh token, code or password anywhere**. With `LOGIN_DETAIL_PROBE_SECRETS` set it writes every secret to that file, so the log and config directory can be searched with `grep -rF -f`. |
| `login_detail_probe5` | The option `store_unknown_usernames` (0.9.0) over real HTTP: **off by default, and off means an unknown name is `null` everywhere, as before**; on: the name as typed with `known_user: false` in the history, event, log line, both 24h sensors, the websocket and the storage file, a known user unchanged, a long name cut to 64 characters with its line break replaced; off again. All switching is done through **Configure** (the options flow) on a loaded entry, and must take effect by a reload of the entry, without a restart. Run on a config with an entry from before 0.9.0 (E'), it also checks that the missing key reads as off and that turning it on keeps the other options. |

The probes write their result to `report.json` / `report2.json` /
`report3.json` / `report4.json` / `report5.json` / `report6.json` in the config
directory.

### How to run it

```sh
# 1. put the integration and the probes into a throwaway config
mkdir -p /usr/src/login_detail_test/custom_components /usr/src/login_detail_test/.storage
cp -a custom_components/login_detail /usr/src/login_detail_test/custom_components/
cp -a tests/custom_components/login_detail_probe  /usr/src/login_detail_test/custom_components/
cp -a tests/custom_components/login_detail_probe2 /usr/src/login_detail_test/custom_components/
cp -a tests/custom_components/login_detail_probe3 /usr/src/login_detail_test/custom_components/
cp -a tests/custom_components/login_detail_probe4 /usr/src/login_detail_test/custom_components/
cp -a tests/custom_components/login_detail_probe5 /usr/src/login_detail_test/custom_components/
cp -a tests/custom_components/login_detail_probe6 /usr/src/login_detail_test/custom_components/
cp tests/configuration.yaml /usr/src/login_detail_test/configuration.yaml

# 2. .storage/http MUST be seeded with a port other than your instance's,
#    otherwise http fails and HA drops into recovery mode.
#    Copy /config/.storage/http and set:
#      server_port: 18123, server_host: ["127.0.0.1"],
#      trusted_proxies: [], ip_ban_enabled: false

# 3. optional: seed .storage/core.entity_registry with your instance's
#    login_detail rows (config_entry_id: null) to exercise the adoption of
#    entities created by a YAML-based setup. Rows from before 0.8.0 carry
#    the Danish entity IDs; the probes then check that those are kept.
#    Unseeded, they check the English IDs a new installation gets.

# 4. run
cd /usr/src/login_detail_test && env -u SUPERVISOR -u SUPERVISOR_TOKEN \
  timeout 240 python -m homeassistant -c /usr/src/login_detail_test --skip-pip --log-no-color
cat /usr/src/login_detail_test/report.json
```

**Run A, then run B on A's result:** copy the directory, remove
`login_detail:` and `login_detail_probe:` from `configuration.yaml`, and add
`login_detail_probe2:`.

**Run C in a fresh directory:** the same setup as A, but with only
`login_detail_probe3:` in `configuration.yaml` (no `login_detail:` line - the
probe adds the integration from the UI). It needs `http` on port 18123 with
`ip_ban_enabled: false`, as above.

**Run D in a fresh directory:** only `login_detail_probe4:` in
`configuration.yaml`, and `http` on port 18123 with IP banning on, a high
threshold and the test client as a trusted proxy - in the seeded
`.storage/http`, or as below in `configuration.yaml` in a directory without
one:

```yaml
http:
  server_port: 18123
  server_host: 127.0.0.1
  ip_ban_enabled: true
  login_attempts_threshold: 1000
  use_x_forwarded_for: true
  trusted_proxies:
    - 127.0.0.1
```

Probe D needs the MCP server's requirements (`mcp`, `aiohttp_sse`, `anyio`)
and `conversation`'s, which a Home Assistant container already has. Its last
check bans 127.0.0.1 in the throwaway config on purpose.

**Run F in a fresh directory:** like run D - only `login_detail_probe6:` in
`configuration.yaml`, with the same `http` block. To search for leaked
secrets afterwards:

```sh
LOGIN_DETAIL_PROBE_SECRETS=/tmp/probe6-secrets.txt python -m homeassistant ...
grep -rlF -f /tmp/probe6-secrets.txt home-assistant.log events6.json \
  report6.json .storage --exclude=auth   # expect no output, exit 1
```

`.storage/auth` is core's own file and holds the refresh tokens that are
still alive, as it does for every session; the probe writes those to
`/tmp/probe6-secrets.txt.core_owned`, and they are the only hits there.

**Run E in a fresh directory:** like run C, with only
`login_detail_probe5:` in `configuration.yaml`, and `http` on port 18123
with `ip_ban_enabled: false`.

**Run E' (an existing installation):** like run E, but seed
`.storage/core.config_entries` with a `login_detail` entry as 0.8.1 left it -
options with `max_entries`, `max_days` and `show_panel`, **without**
`store_unknown_usernames`. The probe then uses that entry instead of adding
one. Seed only that file: a copy of a whole config directory from another
run fails, because the probe creates its test users again.

Expected result for 0.10.0 on Home Assistant 2026.10.1: **A: 35/35, B: 9/9,
C: 25/25, D: 25/25, E: 15/15, E': 16/16, F: 19/19.**

### Two things to get right

**`cd` into the directory first.** `''` is on `sys.path`, and without the
`cd` the loader finds `/config/custom_components` instead - and then you are
testing your running instance.

**Remove the `SUPERVISOR` variables.** Otherwise the test instance inherits
them from the container, starts the Supervisor integration and begins
discovering real devices on the network (Cast, Bluetooth, Thread, USB radios).
The test must not be able to touch anything outside the throwaway config.
