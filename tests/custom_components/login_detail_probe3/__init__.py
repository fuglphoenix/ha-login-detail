"""Acceptance test probe C - the sign-in log of 0.7.0, over real HTTP.

Like the other probes this is NOT part of login_detail and is never installed
on a real instance. It sets the integration up from the UI, then talks to the
throwaway instance the way a browser would - the login flow over HTTP and the
websocket API with real access tokens - and writes report3.json.

All users, passwords, IP addresses and user agents below are invented and
exist only in the throwaway config.
"""

from __future__ import annotations

from datetime import timedelta
import json
import logging
import traceback
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

DOMAIN = "login_detail_probe3"
TARGET = "login_detail"
REPORT = "report3.json"

BASE = "http://127.0.0.1:18123"
CLIENT_ID = f"{BASE}/"
PASSWORD = "correct-horse-battery"
# What a user types into the username field by mistake.
PASSWORD_AS_USERNAME = "Tr0ub4dor&3-typed-in-the-wrong-field"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ProbeBrowser/1.0"
WS_AGENT = "ProbeCompanion/2.0 (fictitious)"

LOG_FIELDS = {
    "ts",
    "type",
    "reason",
    "username",
    "user_id",
    "auth_provider",
    "ip",
    "hostname",
    "endpoint",
    "user_agent",
    "flow_id",
    "count",
    "client_id",
    "client_name",
    "token_type",
}

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
    _LOGGER.warning("LOGIN-DETAIL-PROBE3 %s/%s", passed, len(report.checks))
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


async def _ws(
    session: aiohttp.ClientSession, token: str, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Authenticate and send each message, returning the responses in order."""
    out = []
    async with session.ws_connect(
        f"{BASE}/api/websocket", headers={"User-Agent": WS_AGENT}
    ) as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": token})
        auth = await ws.receive_json()
        if auth.get("type") != "auth_ok":
            return [auth]
        for number, message in enumerate(messages, start=1):
            await ws.send_json({"id": number, **message})
            out.append(await ws.receive_json())
    return out


async def _ws_bad_token(session: aiohttp.ClientSession) -> str | None:
    async with session.ws_connect(
        f"{BASE}/api/websocket", headers={"User-Agent": WS_AGENT}
    ) as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": "not-a-token"})
        return (await ws.receive_json()).get("type")


def _list(**filters: Any) -> dict[str, Any]:
    return {"type": "login_detail/sign_in_log/list", **filters}


async def _checks(hass: HomeAssistant, report: Report) -> None:
    capture = LogCapture()
    logging.getLogger("custom_components.login_detail").addHandler(capture)

    # ---- 1. Setup from the UI, with the panel --------------------------------
    flow = await hass.config_entries.flow.async_init(TARGET, context={"source": "user"})
    await hass.config_entries.flow.async_configure(flow["flow_id"], {})
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(TARGET)[0]
    report.check(
        "C1. entry set up from the UI and loaded",
        entry.state is ConfigEntryState.LOADED,
        str(entry.state),
    )
    data = hass.data[TARGET]
    report.check(
        "C2. default retention is 500 entries / 7 days",
        (data.max_entries, data.max_days) == (500, 7),
        [data.max_entries, data.max_days],
    )
    panel = hass.data.get("frontend_panels", {}).get("login-detail")
    report.check(
        "C3. the panel is registered, admin only, with a versioned module",
        panel is not None
        and panel.require_admin is True
        and panel.sidebar_title == "Sign-in log"
        and f"?v={(await async_get_integration(hass, TARGET)).version}"
        in (panel.config or {}).get("_panel_custom", {}).get("module_url", ""),
        None if panel is None else panel.to_response(),
    )

    session = aiohttp.ClientSession()
    try:
        async with session.get(
            f"{BASE}/login_detail_static/sign-in-log-panel.js"
        ) as resp:
            body = await resp.text()
        report.check(
            "C4. the panel module is served",
            resp.status == 200 and "login-detail-sign-in-log" in body,
            resp.status,
        )

        admin = await _user(hass, "probe-admin", "system-admin")
        member = await _user(hass, "probe-member", "system-users")
        admin_token = await _token(hass, admin)
        member_token = await _token(hass, member)

        # ---- 2. Logins over HTTP: user_id, user agent, username guard --------
        since = dt_util.utcnow()
        await _http_login(session, "probe-member", "wrong")
        await _http_login(session, PASSWORD_AS_USERNAME, "whatever")
        ok = await _http_login(session, "probe-member", PASSWORD)
        await hass.async_block_till_done()
        report.check(
            "C5. a correct login over HTTP completes",
            ok.get("type") == "create_entry",
            ok.get("type"),
        )
        history = data.history
        failed_known = next(
            (
                h
                for h in history
                if h["result"] == "invalid_auth" and h["username"] == "probe-member"
            ),
            None,
        )
        report.check(
            "C6. failed login of a known user: username, user_id and user agent",
            failed_known is not None
            and failed_known.get("user_id") == member.id
            and failed_known.get("user_agent") == USER_AGENT,
            failed_known,
        )
        failed_unknown = [
            h
            for h in history
            if h["result"] == "invalid_auth" and h is not failed_known
        ]
        report.check(
            "C7. failed login of an unknown name: username and user_id are null",
            len(failed_unknown) == 1
            and failed_unknown[0]["username"] is None
            and failed_unknown[0]["user_id"] is None
            and failed_unknown[0]["user_agent"] == USER_AGENT,
            failed_unknown,
        )
        stored = json.dumps(history)
        logged = json.dumps(capture.lines)
        report.check(
            "C8. a password typed as username is nowhere - history, log",
            PASSWORD_AS_USERNAME not in stored and PASSWORD_AS_USERNAME not in logged,
            None,
        )
        success = next((h for h in history if h["result"] == "ok"), None)
        report.check(
            "C9. successful login: username, user_id, user agent",
            success is not None
            and success["username"] == "probe-member"
            and success["user_id"] == member.id
            and success["user_agent"] == USER_AGENT,
            success,
        )

        # ---- 3. Rejected requests carry the user agent and still fold --------
        first = await _ws_bad_token(session)
        second = await _ws_bad_token(session)
        await hass.async_block_till_done()
        rejected = [h for h in data.history if h["result"] == "invalid_request_auth"]
        report.check(
            "C10. two bad websocket tokens: one folded entry, count 2, user agent",
            first == second == "auth_invalid"
            and len(rejected) == 1
            and rejected[0]["count"] == 2
            and rejected[0]["endpoint"] == "/api/websocket"
            and rejected[0]["user_agent"] == WS_AGENT,
            rejected,
        )

        # ---- 4. One log line per event, with all fields -----------------------
        sign_in_lines = [
            (level, line) for level, line in capture.lines if "Sign-in log:" in line
        ]
        fields = (
            "type=",
            "reason=",
            "username=",
            "user_id=",
            "auth_provider=",
            "ip=",
            "endpoint=",
            "user_agent=",
            "flow_id=",
        )
        report.check(
            "C11. one log line per event (2 failed, 1 ok, 2 rejected), all fields",
            len(sign_in_lines) == 5
            and all(all(f in line for f in fields) for _, line in sign_in_lines),
            sign_in_lines,
        )
        report.check(
            "C12. failed logins at WARNING, the rest at INFO",
            [level for level, _ in sign_in_lines]
            == [
                logging.WARNING,
                logging.WARNING,
                logging.INFO,
                logging.INFO,
                logging.INFO,
            ],
            [level for level, _ in sign_in_lines],
        )

        # ---- 5. The websocket command ----------------------------------------
        responses = await _ws(
            session,
            admin_token,
            [
                _list(),
                _list(log_type="auth.login_failed"),
                _list(username="PROBE-member"),
                _list(user_id=member.id),
                _list(ip="127.0."),
                _list(ip="192.0.2."),
                _list(start_time=(since - timedelta(minutes=1)).isoformat()),
                _list(end_time=(since - timedelta(minutes=1)).isoformat()),
                _list(limit=2),
                _list(start_time="yesterday"),
                _list(log_type="auth.something_else"),
            ],
        )
        full = responses[0]
        entries = (full.get("result") or {}).get("entries", [])
        report.check(
            "C13. admin: list succeeds with every entry, in the log format",
            full.get("success") is True
            and len(entries) == 4
            and all(set(e) == LOG_FIELDS for e in entries),
            full,
        )
        report.check(
            "C14. newest first, types mapped",
            [e["type"] for e in entries]
            == [
                "auth.request_rejected",
                "auth.login_success",
                "auth.login_failed",
                "auth.login_failed",
            ]
            and entries[1]["reason"] is None,
            [e["type"] for e in entries],
        )
        counts = [len((r.get("result") or {}).get("entries", [])) for r in responses]
        report.check(
            "C15. filters: type, username, user_id, ip prefix, time span, limit",
            counts[1:9] == [2, 2, 2, 4, 0, 4, 0, 2]
            and responses[8]["result"]["total"] == 4,
            counts,
        )
        report.check(
            "C16. a bad time or type is refused",
            responses[9].get("error", {}).get("code") == "invalid_format"
            and responses[10].get("error", {}).get("code") == "invalid_format",
            [responses[9].get("error"), responses[10].get("error")],
        )
        report.check(
            "C17. the result reports the retention",
            full["result"]["max_entries"] == 500 and full["result"]["max_days"] == 7,
            {k: v for k, v in full["result"].items() if k != "entries"},
        )

        denied = await _ws(session, member_token, [_list()])
        report.check(
            "C18. non-admin: the command is refused with unauthorized, no data",
            denied[0].get("success") is False
            and denied[0].get("error", {}).get("code") == "unauthorized"
            and "result" not in denied[0],
            denied,
        )

        # ---- 6. Retention ----------------------------------------------------
        old = dt_util.utcnow() - timedelta(days=8)
        data.history.append(
            {"timestamp": old.isoformat(), "result": "invalid_auth", "flow_id": "x"}
        )
        data.async_prune_tick()
        report.check(
            "C19. an entry older than max_days is removed by the hourly prune",
            all(h.get("flow_id") != "x" for h in data.history),
            len(data.history),
        )

        options = await hass.config_entries.options.async_init(entry.entry_id)
        options = await hass.config_entries.options.async_configure(
            options["flow_id"],
            {"max_entries": 50, "max_days": 1, "show_panel": False},
        )
        await hass.async_block_till_done()
        data = hass.data[TARGET]
        report.check(
            "C20. options are saved and applied after the reload",
            options.get("type") == "create_entry"
            and (data.max_entries, data.max_days) == (50, 1)
            and hass.config_entries.async_entries(TARGET)[0].state
            is ConfigEntryState.LOADED,
            [options.get("type"), data.max_entries, data.max_days],
        )
        report.check(
            "C21. show_panel off removes the panel",
            "login-detail" not in hass.data.get("frontend_panels", {}),
            None,
        )
        report.check(
            "C22. the history survived the options reload",
            len(data.history) == 4,
            len(data.history),
        )
        now = dt_util.utcnow()
        for number in range(60):
            data.async_record(
                {
                    "timestamp": (now + timedelta(seconds=number)).isoformat(),
                    "result": "invalid_auth",
                    "username": None,
                    "ip_address": None,
                    "flow_id": f"cap-{number}",
                }
            )
        report.check(
            "C23. the history is capped at max_entries, keeping the newest",
            len(data.history) == 50 and data.history[0]["flow_id"] == "cap-59",
            len(data.history),
        )
        too_small = await hass.config_entries.options.async_init(entry.entry_id)
        try:
            await hass.config_entries.options.async_configure(
                too_small["flow_id"],
                {"max_entries": 5, "max_days": 0, "show_panel": True},
            )
            refused = False
        except Exception:  # noqa: BLE001 - schema errors are raised
            refused = True
        report.check("C24. options below the minimum are refused", refused, None)

        # ---- 7. Not loaded ---------------------------------------------------
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        unloaded = await _ws(session, admin_token, [_list()])
        report.check(
            "C25. unloaded: the command answers not_loaded",
            unloaded[0].get("error", {}).get("code") == "not_loaded",
            unloaded,
        )
    finally:
        await session.close()
        logging.getLogger("custom_components.login_detail").removeHandler(capture)
