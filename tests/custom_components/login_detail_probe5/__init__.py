"""Acceptance test probe E - the option store_unknown_usernames of 0.9.0.

Like the other probes this is NOT part of login_detail and is never installed
on a real instance. It sets the integration up from the UI and logs in over
real HTTP with a username no user has - first with the option off (the
default), then on, then off again - and writes report5.json.

Since 0.12.0 the name is kept only in the storage file and the admin-only
sign-in log - never in the event, the log line or a sensor, not even with
`sensor_details` on, which the probe turns on together with the option - and
turning the option off removes the names stored. Last, a user who is not an
administrator reads the states over the REST API and must find no username or
IP address in Login Detail's sensors.

If the throwaway config already has a login_detail entry (a seeded
.storage/core.config_entries from before 0.9.0, whose options lack the key),
the probe uses that entry instead of adding one, and also checks that the
missing key reads as off and that turning it on keeps the other options.

All users, passwords, IP addresses and user agents below are invented and
exist only in the throwaway config.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import traceback
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

DOMAIN = "login_detail_probe5"
TARGET = "login_detail"
REPORT = "report5.json"

BASE = f"http://127.0.0.1:{os.environ.get('HA_PROBE_PORT', '18123')}"
CLIENT_ID = f"{BASE}/"
PASSWORD = "correct-horse-battery"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ProbeBrowser/1.0"
# What a brute force attempt tries.
UNKNOWN = "brute-admin"
# Longer than the cap, with a line break that must not reach the log as one.
LONG = "x" * 30 + "\nINJECTED log line" + "y" * 100
LONG_STORED = ("x" * 30 + "\ufffdINJECTED log line" + "y" * 100)[:64]

_LOGGER = logging.getLogger(__name__)


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def check(self, name: str, ok: bool, detail: Any = None) -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)


class LogCapture(logging.Handler):
    """Collects login_detail's log lines."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[tuple[int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append((record.levelno, record.getMessage()))


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Start the probe once Home Assistant is up."""

    async def _started(event: Any) -> None:
        hass.async_create_task(_run(hass))

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started)
    return True


async def _run(hass: HomeAssistant) -> None:
    report = Report()
    try:
        await _checks(hass, report)
    except Exception:  # noqa: BLE001 - the report must be written regardless
        report.check("the probe ran to completion", False, traceback.format_exc())
    passed = sum(1 for c in report.checks if c["ok"])
    payload = {"passed": passed, "total": len(report.checks), "checks": report.checks}
    await hass.async_add_executor_job(_write, hass.config.path(REPORT), payload)
    _LOGGER.warning("LOGIN-DETAIL-PROBE5 %s/%s", passed, len(report.checks))
    hass.async_create_task(hass.async_stop())


def _write(path: str, payload: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)


async def _user(hass: HomeAssistant, username: str, group: str) -> Any:
    provider = hass.auth.auth_providers[0]
    await provider.async_initialize()
    await provider.async_add_auth(username, PASSWORD)
    credentials = await provider.async_get_or_create_credentials({"username": username})
    user = await hass.auth.async_create_user(username, group_ids=[group])
    await hass.auth.async_link_user(user, credentials)
    return user


async def _token(hass: HomeAssistant, user: Any) -> str:
    refresh = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    return hass.auth.async_create_access_token(refresh)


async def _http_login(
    session: aiohttp.ClientSession, username: str, password: str
) -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT}
    async with session.post(
        f"{BASE}/auth/login_flow",
        json={
            "client_id": CLIENT_ID,
            "handler": ["homeassistant", None],
            "redirect_uri": CLIENT_ID,
        },
        headers=headers,
    ) as resp:
        step = await resp.json()
    async with session.post(
        f"{BASE}/auth/login_flow/{step['flow_id']}",
        json={"client_id": CLIENT_ID, "username": username, "password": password},
        headers=headers,
    ) as resp:
        return await resp.json()


async def _ws_list(session: aiohttp.ClientSession, token: str) -> dict[str, Any]:
    async with session.ws_connect(f"{BASE}/api/websocket") as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": token})
        await ws.receive_json()
        await ws.send_json({"id": 1, "type": "login_detail/sign_in_log/list"})
        return await ws.receive_json()


async def _set_option(hass: HomeAssistant, entry_id: str, on: bool) -> Any:
    """Like Configure in the UI: keep what the form shows, flip the boxes.

    `sensor_details` follows the option, so with it on the probe sees what
    the sensors would show at most.
    """
    flow = await hass.config_entries.options.async_init(entry_id)
    user_input = {key.schema: key.default() for key in flow["data_schema"].schema}
    user_input["store_unknown_usernames"] = on
    user_input["sensor_details"] = on
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], user_input
    )
    await hass.async_block_till_done()
    return result


def _entity_id(hass: HomeAssistant, unique_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id("sensor", TARGET, unique_id)


async def _checks(hass: HomeAssistant, report: Report) -> None:
    capture = LogCapture()
    logging.getLogger("custom_components.login_detail").addHandler(capture)
    events: list[dict[str, Any]] = []
    hass.bus.async_listen("login_detail_failed", lambda e: events.append(dict(e.data)))

    existing = bool(hass.config_entries.async_entries(TARGET))
    if not existing:
        flow = await hass.config_entries.flow.async_init(
            TARGET, context={"source": "user"}
        )
        await hass.config_entries.flow.async_configure(flow["flow_id"], {})
        await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(TARGET)[0]
    data = hass.data[TARGET]
    options_before = dict(entry.options)
    if existing:
        report.check(
            "E0. existing entry from before 0.9.0: no key in options, reads as off",
            entry.state is ConfigEntryState.LOADED
            and "store_unknown_usernames" not in entry.options
            and data.store_unknown_usernames is False,
            options_before,
        )

    # ---- 1. The option, off by default ---------------------------------------
    options_form = await hass.config_entries.options.async_init(entry.entry_id)
    schema = options_form["data_schema"].schema
    default = next(
        (key.default() for key in schema if key == "store_unknown_usernames"), None
    )
    hass.config_entries.options.async_abort(options_form["flow_id"])
    report.check(
        "E1. the options form has store_unknown_usernames, default off",
        default is False and data.store_unknown_usernames is False,
        [default, data.store_unknown_usernames],
    )

    session = aiohttp.ClientSession()
    try:
        admin = await _user(hass, "probe-admin", "system-admin")
        member = await _user(hass, "probe-member", "system-users")
        admin_token = await _token(hass, admin)
        failed_id = _entity_id(hass, "login_detail_failed_24h")
        ip_id = _entity_id(hass, "login_detail_unique_ip_24h")

        # ---- 2. Option off: the unknown name is not kept, as before ----------
        await _http_login(session, UNKNOWN, "guess-1")
        await hass.async_block_till_done()
        off = data.history[0]
        report.check(
            "E2. off: unknown name - username and user_id null, no known_user key",
            off["result"] == "invalid_auth"
            and off["username"] is None
            and off["user_id"] is None
            and "known_user" not in off,
            off,
        )
        report.check(
            "E3. off: the event has username null and no known_user key",
            len(events) == 1
            and events[0]["username"] is None
            and "known_user" not in events[0],
            events,
        )
        report.check(
            "E4. off: the name is not in the log",
            UNKNOWN not in json.dumps(capture.lines)
            and "known_user" not in json.dumps(capture.lines),
            capture.lines,
        )

        # ---- 3. Option on ----------------------------------------------------
        old_data = data
        result = await _set_option(hass, entry.entry_id, True)
        data = hass.data[TARGET]
        entry = hass.config_entries.async_entries(TARGET)[0]
        report.check(
            "E5. on: saved via Configure, applied by a reload of the entry"
            " (no restart), the other options kept",
            result.get("type") == "create_entry"
            and data is not old_data
            and data.store_unknown_usernames is True
            and entry.options["store_unknown_usernames"] is True
            and all(
                entry.options.get(k) == v
                for k, v in options_before.items()
                if k != "sensor_details"
            )
            and entry.state is ConfigEntryState.LOADED,
            [result.get("type"), dict(entry.options), options_before],
        )
        capture.lines.clear()
        events.clear()
        await _http_login(session, UNKNOWN, "guess-2")
        await _http_login(session, "probe-member", "wrong")
        await _http_login(session, LONG, "guess-3")
        await hass.async_block_till_done()
        unknown, known, long_entry = data.history[2], data.history[1], data.history[0]
        report.check(
            "E6. on: unknown name stored as typed, user_id null, known_user false",
            unknown["username"] == UNKNOWN
            and unknown["user_id"] is None
            and unknown["known_user"] is False
            and unknown["user_agent"] == USER_AGENT,
            unknown,
        )
        report.check(
            "E7. on: a known user is unchanged - stored name, user_id, no flag",
            known["username"] == "probe-member"
            and known["user_id"] == member.id
            and "known_user" not in known,
            known,
        )
        report.check(
            "E8. on: a long name is cut to 64 characters, the line break replaced",
            long_entry["username"] == LONG_STORED
            and len(long_entry["username"]) == 64
            and long_entry["known_user"] is False,
            long_entry,
        )
        report.check(
            "E9. on: the event has known_user false, but not the name (0.12.0)",
            [(e["username"], e.get("known_user")) for e in events]
            == [(None, False), ("probe-member", None), (None, False)],
            events,
        )
        lines = [(lvl, line) for lvl, line in capture.lines if "Sign-in log:" in line]
        report.check(
            "E10. on: one WARNING line each; the unknown name flagged, not written",
            len(lines) == 3
            and all(lvl == logging.WARNING for lvl, _ in lines)
            and "username=None" in lines[0][1]
            and lines[0][1].endswith("known_user=False")
            and "known_user" not in lines[1][1]
            and "\n" not in lines[2][1]
            and lines[2][1].endswith("known_user=False")
            and UNKNOWN not in json.dumps(capture.lines)
            and "INJECTED" not in json.dumps(capture.lines),
            lines,
        )
        failed_state = hass.states.get(failed_id) if failed_id else None
        ip_state = hass.states.get(ip_id) if ip_id else None
        report.check(
            "E11. on, with sensor_details: the failed-24h sensor tallies the"
            " known user, never the unknown names",
            failed_state is not None
            and "probe-member" in json.dumps(failed_state.attributes.get("usernames"))
            and UNKNOWN not in json.dumps(failed_state.attributes)
            and "INJECTED" not in json.dumps(failed_state.attributes),
            None if failed_state is None else failed_state.attributes,
        )
        report.check(
            "E12. on, with sensor_details: the IP address sensor never lists the"
            " unknown names",
            ip_state is not None
            and "probe-member" in json.dumps(ip_state.attributes.get("addresses"))
            and UNKNOWN not in json.dumps(ip_state.attributes)
            and "INJECTED" not in json.dumps(ip_state.attributes),
            None if ip_state is None else ip_state.attributes,
        )
        listed = await _ws_list(session, admin_token)
        entries = (listed.get("result") or {}).get("entries", [])
        flagged = [e for e in entries if e.get("known_user") is False]
        report.check(
            "E13. on: the sign-in log flags exactly the two unknown names",
            sorted(e["username"] for e in flagged) == sorted([UNKNOWN, LONG_STORED])
            and all("known_user" not in e for e in entries if e not in flagged),
            entries,
        )
        await data.async_flush()
        stored = await hass.async_add_executor_job(
            Path(hass.config.path(".storage", "login_detail_history")).read_text,
            "utf-8",
        )
        report.check(
            "E14. on: the storage file has the name and the flag",
            UNKNOWN in stored and '"known_user": false' in stored,
            None,
        )

        # ---- 4. Option off again: back to null, the stored names gone --------
        await _set_option(hass, entry.entry_id, False)
        data = hass.data[TARGET]
        events.clear()
        await _http_login(session, "another-guess", "guess-4")
        await hass.async_block_till_done()
        again = data.history[0]
        await data.async_flush()
        stored = await hass.async_add_executor_job(
            Path(hass.config.path(".storage", "login_detail_history")).read_text,
            "utf-8",
        )
        report.check(
            "E15. off again: a new unknown name is null; the names stored while"
            " it was on are removed (0.12.0), the entries kept",
            again["username"] is None
            and "known_user" not in again
            and events[0]["username"] is None
            and len(data.history) == 5
            and not any(h.get("username") == UNKNOWN for h in data.history)
            and not any("known_user" in h for h in data.history)
            and UNKNOWN not in stored
            and "known_user" not in stored,
            again,
        )

        # ---- 5. What a user who is not an administrator sees (0.12.0) --------
        member_token = await _token(hass, member)
        async with session.get(
            f"{BASE}/api/states",
            headers={"Authorization": f"Bearer {member_token}"},
        ) as resp:
            status = resp.status
            states = await resp.json() if status == 200 else []
        ours = {
            unique_id: _entity_id(hass, unique_id)
            for unique_id in (
                "login_detail_latest",
                "login_detail_ok_24h",
                "login_detail_failed_24h",
                "login_detail_unique_ip_24h",
                "login_detail_history",
                "login_detail_sessions",
            )
        }
        shown = [s for s in states if s.get("entity_id") in ours.values()]
        text = json.dumps(shown)
        report.check(
            "E16. a user who is not an administrator reads all six sensors, with"
            " no username, IP address or session user in them",
            status == 200
            and len(shown) == 6
            and not any(
                value in text
                for value in ("probe-member", "probe-admin", "127.0.0.1", UNKNOWN)
            ),
            [status, shown],
        )
    finally:
        await session.close()
        logging.getLogger("custom_components.login_detail").removeHandler(capture)
