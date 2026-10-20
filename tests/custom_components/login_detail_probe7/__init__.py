"""Acceptance test probe G - the request that got the IP address banned (0.11.0).

Like the other probes this is NOT part of login_detail and is never installed
on a real instance. It sets the integration up from the UI and sends rejected
requests and wrong passwords over real HTTP, each from its own address
through the trusted proxy 127.0.0.1 (X-Forwarded-For). Core's failed-attempt
counter is set to threshold - 1 for the addresses that are to be banned, so
the next attempt bans them. It writes report7.json.

It needs `http` with `ip_ban_enabled: true`, a high
`login_attempts_threshold` (1000), `use_x_forwarded_for: true` and
`trusted_proxies: [127.0.0.1]` - the same block as probes D and F. It bans
two TEST-NET addresses in the throwaway config on purpose; 127.0.0.1 itself
is never banned.

All users and tokens below are invented and exist only in the throwaway
config.
"""

from __future__ import annotations

from ipaddress import ip_address
import json
import logging
import traceback
from typing import Any

import aiohttp
from homeassistant.components.auth import login_flow as auth_login_flow
from homeassistant.components.http import ban as http_ban
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

DOMAIN = "login_detail_probe7"
TARGET = "login_detail"
REPORT = "report7.json"
EVENT_REJECTED = "login_detail_request_rejected"
EVENT_FAILED = "login_detail_failed"

BASE = "http://127.0.0.1:18123"
PASSWORD = "correct-horse-battery"
WRONG_PASSWORD = "wrong-password-81c3"
CLIENT_ID = "https://app.example/"

# TEST-NET addresses, sent through the trusted proxy as X-Forwarded-For.
REST_BANNED = "203.0.113.10"  # one rejected REST call at threshold - 1
REST_PLAIN = "198.51.100.20"  # one rejected REST call, counter at 0
FLOW_BANNED = "203.0.113.20"  # one wrong password at threshold - 1
FLOW_PLAIN = "198.51.100.30"  # one wrong password, counter at 0

_LOGGER = logging.getLogger(__name__)


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def check(self, name: str, ok: bool, detail: Any = None) -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)


class LogCapture(logging.Handler):
    """Collects every log record of the instance, with its level."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def sign_in_lines(self) -> list[tuple[str, str]]:
        return [
            (record.levelname, record.getMessage())
            for record in self.records
            if "Sign-in log:" in record.getMessage()
        ]


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
    _LOGGER.warning("LOGIN-DETAIL-PROBE7 %s/%s", passed, len(report.checks))
    hass.async_create_task(hass.async_stop())


def _write(path: str, payload: Any) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


async def _user(hass: HomeAssistant, username: str, group: str) -> Any:
    provider = hass.auth.auth_providers[0]
    await provider.async_initialize()
    await provider.async_add_auth(username, PASSWORD)
    credentials = await provider.async_get_or_create_credentials({"username": username})
    user = await hass.auth.async_create_user(username, group_ids=[group])
    await hass.auth.async_link_user(user, credentials)
    return user


def _ours(function: Any) -> bool:
    return "login_detail" in getattr(function, "__module__", "")


async def _checks(hass: HomeAssistant, report: Report) -> None:
    capture = LogCapture()
    logging.getLogger().addHandler(capture)
    logging.getLogger("custom_components.login_detail").setLevel(logging.DEBUG)

    events: list[dict[str, Any]] = []

    def _on_event(event: Event) -> None:
        events.append({"event_type": event.event_type, **event.data})

    hass.bus.async_listen(EVENT_REJECTED, _on_event)
    hass.bus.async_listen(EVENT_FAILED, _on_event)

    def _from(ip: str) -> list[dict[str, Any]]:
        return [e for e in events if e.get("ip_address") == ip]

    # ---- 1. Setup ---------------------------------------------------------
    flow = await hass.config_entries.flow.async_init(TARGET, context={"source": "user"})
    if flow.get("type") == "form":
        await hass.config_entries.flow.async_configure(flow["flow_id"], {})
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(TARGET)[0]
    data = hass.data[TARGET]
    app = hass.http.app
    threshold = app[http_ban.KEY_LOGIN_THRESHOLD]
    counter = app[http_ban.KEY_FAILED_LOGIN_ATTEMPTS]
    bans = app[http_ban.KEY_BAN_MANAGER].ip_bans_lookup
    report.check(
        "G1. loaded with IP banning on; process_wrong_login patched in ban.py, "
        "the websocket and login_flow.py",
        entry.state is ConfigEntryState.LOADED
        and threshold == 1000
        and _ours(http_ban.process_wrong_login)
        and _ours(auth_login_flow.process_wrong_login),
        [str(entry.state), threshold],
    )

    admin = await _user(hass, "probe-admin", "system-admin")
    refresh = await hass.auth.async_create_refresh_token(admin, CLIENT_ID)
    admin_valid = hass.auth.async_create_access_token(refresh)

    session = aiohttp.ClientSession()
    try:

        async def _states(ip: str, agent: str, token: str | None = None) -> int:
            headers = {"User-Agent": agent, "X-Forwarded-For": ip}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            async with session.get(f"{BASE}/api/states", headers=headers) as resp:
                await resp.read()
                return resp.status

        async def _wrong_password(ip: str, agent: str) -> tuple[str | None, int]:
            headers = {"User-Agent": agent, "X-Forwarded-For": ip}
            async with session.post(
                f"{BASE}/auth/login_flow",
                json={
                    "client_id": f"{BASE}/",
                    "handler": ["homeassistant", None],
                    "redirect_uri": f"{BASE}/?auth_callback=1",
                },
                headers=headers,
            ) as resp:
                flow_id = (await resp.json()).get("flow_id")
            async with session.post(
                f"{BASE}/auth/login_flow/{flow_id}",
                json={
                    "client_id": f"{BASE}/",
                    "username": "probe-admin",
                    "password": WRONG_PASSWORD,
                },
                headers=headers,
            ) as resp:
                await resp.read()
                return flow_id, resp.status

        # ---- 2. REST below the threshold: a plain rejection -----------------
        status = await _states(REST_PLAIN, "Probe/G-rest-plain")
        await hass.async_block_till_done()
        plain = _from(REST_PLAIN)
        report.check(
            "G2. a rejected REST call below the threshold: reason "
            "invalid_request_auth, no attempts or threshold, not banned",
            status == 401
            and len(plain) == 1
            and plain[0]["reason"] == "invalid_request_auth"
            and "attempts" not in plain[0]
            and ip_address(REST_PLAIN) not in bans,
            [status, plain],
        )

        # ---- 3. REST at threshold - 1: this call bans ------------------------
        counter[ip_address(REST_BANNED)] = threshold - 1
        status = await _states(REST_BANNED, "Probe/G-rest-ban")
        await hass.async_block_till_done()
        then = await _states(REST_BANNED, "Probe/G-rest-after", admin_valid)
        banned = _from(REST_BANNED)
        report.check(
            "G3. the REST call that bans: reason ip_banned_now, rejection "
            "no_credentials, attempts and threshold; the next valid call gets 403",
            status == 401
            and then == 403
            and ip_address(REST_BANNED) in bans
            and len(banned) == 1
            and banned[0]["event_type"] == EVENT_REJECTED
            and banned[0]["reason"] == "ip_banned_now"
            and banned[0]["rejection"] == "no_credentials"
            and banned[0]["endpoint"] == "/api/states"
            and banned[0]["attempts"] == threshold
            and banned[0]["threshold"] == threshold,
            [status, then, banned],
        )
        rest_entries = [e for e in data.history if e.get("ip_address") == REST_BANNED]
        report.check(
            "G4. its history entry: invalid_request_auth with ip_banned_now, "
            "attempts, threshold and count 1",
            len(rest_entries) == 1
            and rest_entries[0]["result"] == "invalid_request_auth"
            and rest_entries[0]["reason"] == "no_credentials"
            and rest_entries[0].get("ip_banned_now") is True
            and rest_entries[0]["attempts"] == threshold
            and rest_entries[0]["count"] == 1,
            rest_entries,
        )

        # ---- 4. Login flow below the threshold: reported once ---------------
        _, status = await _wrong_password(FLOW_PLAIN, "Probe/G-flow-plain")
        await hass.async_block_till_done()
        flow_plain = _from(FLOW_PLAIN)
        report.check(
            "G5. a wrong password below the threshold: login_detail_failed "
            "only - no rejected request on top - and counted by core",
            status == 200
            and [e["event_type"] for e in flow_plain] == [EVENT_FAILED]
            and counter.get(ip_address(FLOW_PLAIN)) == 1
            and ip_address(FLOW_PLAIN) not in bans,
            [status, flow_plain, counter.get(ip_address(FLOW_PLAIN))],
        )

        # ---- 5. Login flow at threshold - 1: this wrong password bans -------
        counter[ip_address(FLOW_BANNED)] = threshold - 1
        flow_id, status = await _wrong_password(FLOW_BANNED, "Probe/G-flow-ban")
        await hass.async_block_till_done()
        flow_banned = _from(FLOW_BANNED)
        failed = [e for e in flow_banned if e["event_type"] == EVENT_FAILED]
        rejected = [e for e in flow_banned if e["event_type"] == EVENT_REJECTED]
        report.check(
            "G6. the wrong password that bans: login_detail_failed with the "
            "username, plus one login_detail_request_rejected with reason "
            "ip_banned_now, the flow ID, attempts and threshold",
            status == 200
            and ip_address(FLOW_BANNED) in bans
            and len(failed) == 1
            and failed[0]["username"] == "probe-admin"
            and len(rejected) == 1
            and rejected[0]["reason"] == "ip_banned_now"
            and rejected[0]["rejection"] == "invalid_auth_request"
            and rejected[0]["endpoint"] == "/auth/login_flow"
            and rejected[0]["flow_id"] == flow_id
            and rejected[0]["username"] is None
            and rejected[0]["attempts"] == threshold
            and rejected[0]["threshold"] == threshold,
            [status, flow_banned],
        )
        flow_entries = [e for e in data.history if e.get("ip_address") == FLOW_BANNED]
        report.check(
            "G7. one history entry for that attempt: the failed login itself, "
            "marked ip_banned_now with attempts and threshold",
            len(flow_entries) == 1
            and flow_entries[0]["result"] == "invalid_auth"
            and flow_entries[0]["username"] == "probe-admin"
            and flow_entries[0]["flow_id"] == flow_id
            and flow_entries[0].get("ip_banned_now") is True
            and flow_entries[0]["attempts"] == threshold,
            flow_entries,
        )
        async with session.post(
            f"{BASE}/auth/login_flow",
            json={
                "client_id": f"{BASE}/",
                "handler": ["homeassistant", None],
                "redirect_uri": f"{BASE}/?auth_callback=1",
            },
            headers={
                "User-Agent": "Probe/G-flow-after",
                "X-Forwarded-For": FLOW_BANNED,
            },
        ) as resp:
            await resp.read()
            after = resp.status
        report.check(
            "G8. the banned address gets 403 on the login flow afterwards",
            after == 403,
            after,
        )

        # ---- 6. Log lines ---------------------------------------------------
        lines = capture.sign_in_lines()
        banned_lines = [
            (level, line) for level, line in lines if "ip_banned_now" in line
        ]
        report.check(
            "G9. the two bans are logged at WARNING with ip_banned_now=True, "
            "attempts and threshold; plain rejections stay INFO",
            len(banned_lines) == 2
            and all(level == "WARNING" for level, _ in banned_lines)
            and all(
                f"ip_banned_now=True attempts={threshold} threshold={threshold}" in line
                for _, line in banned_lines
            )
            and any(
                level == "INFO" and f"ip={REST_PLAIN}" in line for level, line in lines
            ),
            banned_lines,
        )

        # ---- 7. Sensor: banned per IP ---------------------------------------
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "sensor", TARGET, "login_detail_unique_ip_24h"
        )
        state = hass.states.get(entity_id) if entity_id else None
        addresses = (state.attributes.get("addresses") or {}) if state else {}
        report.check(
            "G10. Login IP addresses 24h: banned true for the two banned "
            "addresses, false for the other two",
            all(
                addresses.get(ip, {}).get("banned") is True
                for ip in (REST_BANNED, FLOW_BANNED)
            )
            and all(
                addresses.get(ip, {}).get("banned") is False
                for ip in (REST_PLAIN, FLOW_PLAIN)
            ),
            [entity_id, addresses],
        )
        history_id = registry.async_get_entity_id(
            "sensor", TARGET, "login_detail_history"
        )
        history_state = hass.states.get(history_id) if history_id else None
        shown = (history_state.attributes.get("events") or []) if history_state else []
        marked = [e for e in shown if e.get("ip_banned_now")]
        report.check(
            "G11. Login history shows ip_banned_now, attempts and threshold on "
            "the two ban entries only",
            len(marked) == 2
            and all(e["attempts"] == threshold for e in marked)
            and all("attempts" not in e for e in shown if not e.get("ip_banned_now")),
            marked,
        )

        # ---- 8. Websocket, storage, panel ---------------------------------
        async with session.ws_connect(f"{BASE}/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": admin_valid})
            await ws.receive_json()
            await ws.send_json(
                {"id": 1, "type": "login_detail/sign_in_log/list", "limit": 100}
            )
            listed = await ws.receive_json()
        entries = listed.get("result", {}).get("entries", [])
        logged = [e for e in entries if e.get("ip_banned_now")]
        report.check(
            "G12. sign_in_log/list: ip_banned_now, attempts and threshold on the "
            "two ban entries (types request_rejected and login_failed), absent "
            "elsewhere",
            sorted(e["type"] for e in logged)
            == ["auth.login_failed", "auth.request_rejected"]
            and all(e["threshold"] == threshold for e in logged)
            and all("ip_banned_now" not in e for e in entries if e not in logged),
            logged,
        )
        await data.async_flush()
        stored = json.loads(
            await hass.async_add_executor_job(
                _read, hass.config.path(".storage", "login_detail_history")
            )
        )
        stored_marked = [e for e in stored["data"]["history"] if e.get("ip_banned_now")]
        report.check(
            "G13. the storage file keeps the mark on both entries",
            sorted(e["ip_address"] for e in stored_marked)
            == sorted([REST_BANNED, FLOW_BANNED]),
            stored_marked,
        )
        async with session.get(
            f"{BASE}/login_detail_static/sign-in-log-panel.js"
        ) as resp:
            panel = await resp.text()
        report.check(
            "G14. the panel marks the entry and shows attempts and threshold",
            resp.status == 200 and "IP banned" in panel and "Ban threshold" in panel,
            resp.status,
        )

        # ---- 9. Unload takes the login_flow patch off, reload puts it back --
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        off = auth_login_flow.process_wrong_login is http_ban.process_wrong_login
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        report.check(
            "G15. unload restores core's process_wrong_login in login_flow.py; "
            "setting the entry up again patches it again",
            off
            and _ours(http_ban.process_wrong_login)
            and _ours(auth_login_flow.process_wrong_login),
            [off, entry.state.value],
        )
    finally:
        await session.close()
        logging.getLogger().removeHandler(capture)
