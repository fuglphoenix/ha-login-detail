"""Acceptance test probe E - the option store_unknown_usernames of 0.9.0.

Like the other probes this is NOT part of login_detail and is never installed
on a real instance. It sets the integration up from the UI and logs in over
real HTTP with a username no user has - first with the option off (the
default), then on, then off again - and writes report5.json.

All users, passwords, IP addresses and user agents below are invented and
exist only in the throwaway config.
"""

from __future__ import annotations

import json
import logging
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

BASE = "http://127.0.0.1:18123"
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
    flow = await hass.config_entries.options.async_init(entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "max_entries": 500,
            "max_days": 7,
            "show_panel": True,
            "store_unknown_usernames": on,
        },
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

    flow = await hass.config_entries.flow.async_init(TARGET, context={"source": "user"})
    await hass.config_entries.flow.async_configure(flow["flow_id"], {})
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(TARGET)[0]
    data = hass.data[TARGET]

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
        result = await _set_option(hass, entry.entry_id, True)
        data = hass.data[TARGET]
        report.check(
            "E5. on: the option is saved and applied after the reload",
            result.get("type") == "create_entry"
            and data.store_unknown_usernames is True
            and hass.config_entries.async_entries(TARGET)[0].state
            is ConfigEntryState.LOADED,
            [result.get("type"), data.store_unknown_usernames],
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
            "E9. on: the event carries the name and known_user false",
            [(e["username"], e.get("known_user")) for e in events]
            == [(UNKNOWN, False), ("probe-member", None), (LONG_STORED, False)],
            events,
        )
        lines = [(lvl, line) for lvl, line in capture.lines if "Sign-in log:" in line]
        report.check(
            "E10. on: one WARNING line each; the unknown name quoted and flagged",
            len(lines) == 3
            and all(lvl == logging.WARNING for lvl, _ in lines)
            and f"username='{UNKNOWN}'" in lines[0][1]
            and lines[0][1].endswith("known_user=False")
            and "known_user" not in lines[1][1]
            and "\n" not in lines[2][1]
            and lines[2][1].endswith("known_user=False"),
            lines,
        )
        failed_state = hass.states.get(failed_id) if failed_id else None
        ip_state = hass.states.get(ip_id) if ip_id else None
        report.check(
            "E11. on: the failed-24h sensor tallies the unknown name",
            failed_state is not None
            and UNKNOWN in json.dumps(failed_state.attributes.get("usernames")),
            None if failed_state is None else failed_state.attributes,
        )
        report.check(
            "E12. on: the IP address sensor lists the unknown name",
            ip_state is not None
            and UNKNOWN in json.dumps(ip_state.attributes.get("addresses")),
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

        # ---- 4. Option off again: back to null --------------------------------
        await _set_option(hass, entry.entry_id, False)
        data = hass.data[TARGET]
        events.clear()
        await _http_login(session, "another-guess", "guess-4")
        await hass.async_block_till_done()
        again = data.history[0]
        report.check(
            "E15. off again: a new unknown name is null; older entries are kept",
            again["username"] is None
            and "known_user" not in again
            and events[0]["username"] is None
            and any(h.get("username") == UNKNOWN for h in data.history),
            again,
        )
    finally:
        await session.close()
        logging.getLogger("custom_components.login_detail").removeHandler(capture)
