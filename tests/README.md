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

The probes write their result to `report.json` / `report2.json` /
`report3.json` in the config directory.

### How to run it

```sh
# 1. put the integration and the probes into a throwaway config
mkdir -p /usr/src/login_detail_test/custom_components /usr/src/login_detail_test/.storage
cp -a custom_components/login_detail /usr/src/login_detail_test/custom_components/
cp -a tests/custom_components/login_detail_probe  /usr/src/login_detail_test/custom_components/
cp -a tests/custom_components/login_detail_probe2 /usr/src/login_detail_test/custom_components/
cp -a tests/custom_components/login_detail_probe3 /usr/src/login_detail_test/custom_components/
cp tests/configuration.yaml /usr/src/login_detail_test/configuration.yaml

# 2. .storage/http MUST be seeded with a port other than your instance's,
#    otherwise http fails and HA drops into recovery mode.
#    Copy /config/.storage/http and set:
#      server_port: 18123, server_host: ["127.0.0.1"],
#      trusted_proxies: [], ip_ban_enabled: false

# 3. optional: seed .storage/core.entity_registry with your instance's
#    login_detail rows (config_entry_id: null) to exercise the adoption of
#    entities created by a YAML-based setup.

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

Expected result for 0.7.0 on Home Assistant 2026.10.1: **A: 35/35, B: 9/9,
C: 25/25.**

### Two things to get right

**`cd` into the directory first.** `''` is on `sys.path`, and without the
`cd` the loader finds `/config/custom_components` instead - and then you are
testing your running instance.

**Remove the `SUPERVISOR` variables.** Otherwise the test instance inherits
them from the container, starts the Supervisor integration and begins
discovering real devices on the network (Cast, Bluetooth, Thread, USB radios).
The test must not be able to touch anything outside the throwaway config.
