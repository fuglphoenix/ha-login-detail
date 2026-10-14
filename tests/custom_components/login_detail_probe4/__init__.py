"""Acceptance test probe D - why a request was rejected, and by whom (0.7.1).

Like the other probes this is NOT part of login_detail and is never installed
on a real instance. It sets the integration and the MCP server ("require
admin", the default) up from the UI, sends rejected requests over real HTTP
and websocket - without a token, with expired, forged, revoked and garbage
tokens, as a deactivated user and as a non-admin - and writes report4.json.

It needs `http` with `ip_ban_enabled: true` and a high
`login_attempts_threshold` (1000): without the ban middleware, core does not
report a rejected request on a REST view at all. It also needs
`use_x_forwarded_for: true` and `trusted_proxies: [127.0.0.1]`, to play a
local-only user connecting from outside. The last check bans the
probe's own IP address on purpose.

All users, clients and tokens below are invented and exist only in the
throwaway config.
"""

from __future__ import annotations

from datetime import timedelta
from ipaddress import ip_address
import json
import logging
import time
import traceback
from typing import Any

import aiohttp
from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
from homeassistant.components.http.ban import (
    KEY_FAILED_LOGIN_ATTEMPTS,
    KEY_LOGIN_THRESHOLD,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.typing import ConfigType
import jwt

DOMAIN = "login_detail_probe4"
TARGET = "login_detail"
REPORT = "report4.json"
EVENT = "login_detail_request_rejected"

BASE = "http://127.0.0.1:18123"
MCP = f"{BASE}/api/mcp"
PASSWORD = "correct-horse-battery"
CLIENT_ID = "https://mcp-client.example"
LLAT_NAME = "Probe wall tablet"
LOCALHOST = ip_address("127.0.0.1")
# Outside the local network (TEST-NET-3), sent through the trusted proxy
# 127.0.0.1 as X-Forwarded-For.
OUTSIDE = "203.0.113.7"

MCP_BODY = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}

_LOGGER = logging.getLogger(__name__)


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def check(self, name: str, ok: bool, detail: Any = None) -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)


class LogCapture(logging.Handler):
    """Collects every log line of the instance, not just login_detail's."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


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
    _LOGGER.warning("LOGIN-DETAIL-PROBE4 %s/%s", passed, len(report.checks))
    hass.async_create_task(hass.async_stop())


def _write(path: str, payload: dict[str, Any]) -> None:
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


def _signed(key: str, issuer: str, expires_in: int) -> str:
    """A token as core makes them, valid for `expires_in` seconds (or past)."""
    now = int(time.time())
    return jwt.encode(
        {"iss": issuer, "iat": now - 3600, "exp": now + expires_in},
        key,
        algorithm="HS256",
    )


def _expired(refresh: Any) -> str:
    return _signed(refresh.jwt_key, refresh.id, -1800)


def _pieces(token: str) -> list[str]:
    """The token and every part of it that must not leak."""
    return [token, *(part for part in token.split(".") if len(part) >= 8)]


async def _mcp(
    session: aiohttp.ClientSession,
    agent: str,
    authorization: str | None = None,
    forwarded_for: str | None = None,
) -> aiohttp.ClientResponse:
    headers = {"User-Agent": agent}
    if authorization is not None:
        headers["Authorization"] = authorization
    if forwarded_for is not None:
        headers["X-Forwarded-For"] = forwarded_for
    async with session.post(MCP, json=MCP_BODY, headers=headers) as resp:
        await resp.read()
        return resp


async def _ws_auth(
    session: aiohttp.ClientSession, agent: str, message: dict[str, Any]
) -> str | None:
    async with session.ws_connect(
        f"{BASE}/api/websocket", headers={"User-Agent": agent}
    ) as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", **message})
        return (await ws.receive_json()).get("type")


async def _ws(
    session: aiohttp.ClientSession, token: str, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out = []
    async with session.ws_connect(f"{BASE}/api/websocket") as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": token})
        auth = await ws.receive_json()
        if auth.get("type") != "auth_ok":
            return [auth]
        for number, message in enumerate(messages, start=1):
            await ws.send_json({"id": number, **message})
            out.append(await ws.receive_json())
    return out


def _attributed(event: dict[str, Any] | None, user: Any, **expected: Any) -> bool:
    """The event names this user, with the expected client fields."""
    if event is None:
        return False
    fields = {
        "user_id": user.id,
        "username": user.name,
        "client_id": None,
        "client_name": None,
        "token_type": None,
        **expected,
    }
    return all(event.get(key) == value for key, value in fields.items())


def _anonymous(event: dict[str, Any] | None) -> bool:
    """The event names nobody."""
    return event is not None and all(
        event.get(key) is None
        for key in ("user_id", "username", "client_id", "client_name", "token_type")
    )


async def _checks(hass: HomeAssistant, report: Report) -> None:
    capture = LogCapture()
    logging.getLogger().addHandler(capture)
    logging.getLogger("custom_components.login_detail").setLevel(logging.DEBUG)

    events: list[dict[str, Any]] = []

    def _on_event(event: Event) -> None:
        events.append(dict(event.data))

    hass.bus.async_listen(EVENT, _on_event)

    def _event(agent: str) -> dict[str, Any] | None:
        found = [e for e in events if e.get("user_agent") == agent]
        return found[-1] if len(found) == 1 else None

    # ---- 1. Setup ------------------------------------------------------------
    for domain in (TARGET, "mcp_server"):
        flow = await hass.config_entries.flow.async_init(
            domain, context={"source": "user"}
        )
        if flow.get("type") == "form":
            await hass.config_entries.flow.async_configure(flow["flow_id"], {})
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(TARGET)[0]
    mcp_entry = hass.config_entries.async_entries("mcp_server")[0]
    app = hass.http.app
    report.check(
        "D1. login_detail and the MCP server (require admin) are loaded, "
        "IP ban counting is on",
        entry.state is ConfigEntryState.LOADED
        and mcp_entry.state is ConfigEntryState.LOADED
        and mcp_entry.data.get("require_admin") is True
        and app.get(KEY_LOGIN_THRESHOLD, 0) >= 1,
        [str(entry.state), str(mcp_entry.state), app.get(KEY_LOGIN_THRESHOLD)],
    )
    data = hass.data[TARGET]

    admin = await _user(hass, "probe-admin", "system-admin")
    member = await _user(hass, "probe-member", "system-users")
    leaver = await _user(hass, "probe-leaver", "system-admin")
    homebody = await _user(hass, "probe-homebody", "system-admin")
    await hass.auth.async_update_user(homebody, local_only=True)

    member_refresh = await hass.auth.async_create_refresh_token(member, CLIENT_ID)
    admin_refresh = await hass.auth.async_create_refresh_token(admin, CLIENT_ID)
    llat_refresh = await hass.auth.async_create_refresh_token(
        admin,
        client_name=LLAT_NAME,
        token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN,
        access_token_expiration=timedelta(days=3650),
    )
    leaver_refresh = await hass.auth.async_create_refresh_token(leaver, CLIENT_ID)
    revoked_refresh = await hass.auth.async_create_refresh_token(admin, CLIENT_ID)
    homebody_refresh = await hass.auth.async_create_refresh_token(homebody, CLIENT_ID)

    member_valid = hass.auth.async_create_access_token(member_refresh)
    admin_valid = hass.auth.async_create_access_token(admin_refresh)
    admin_expired = _expired(admin_refresh)
    llat_expired = _expired(llat_refresh)
    leaver_valid = hass.auth.async_create_access_token(leaver_refresh)
    revoked_valid = hass.auth.async_create_access_token(revoked_refresh)
    homebody_valid = hass.auth.async_create_access_token(homebody_refresh)
    forged = _signed("a-key-that-is-not-the-refresh-tokens" * 2, admin_refresh.id, 600)
    unsigned = jwt.encode(
        {"iss": admin_refresh.id, "iat": int(time.time()), "exp": 9999999999},
        None,
        algorithm="none",
    )
    unknown_issuer = _signed(admin_refresh.jwt_key, "0" * 32, -1800)
    secrets = [
        admin_valid,
        member_valid,
        admin_expired,
        llat_expired,
        leaver_valid,
        revoked_valid,
        homebody_valid,
        forged,
        unsigned,
        unknown_issuer,
    ]

    # Core removes a deactivated user's refresh tokens, so its tokens become
    # unknown - like a revoked one.
    await hass.auth.async_deactivate_user(leaver)
    hass.auth.async_remove_refresh_token(revoked_refresh)

    session = aiohttp.ClientSession()
    try:
        # ---- 2. Type 1: no credentials ---------------------------------------
        before = app[KEY_FAILED_LOGIN_ATTEMPTS][LOCALHOST]
        resp = await _mcp(session, "Probe/no-credentials-1")
        after = app[KEY_FAILED_LOGIN_ATTEMPTS][LOCALHOST]
        await hass.async_block_till_done()
        event = _event("Probe/no-credentials-1")
        report.check(
            "D2. MCP call without a token: 401 with WWW-Authenticate, "
            "no_credentials, nobody named",
            resp.status == 401
            and "resource_metadata" in resp.headers.get("WWW-Authenticate", "")
            and event is not None
            and event["rejection"] == "no_credentials"
            and event["reason"] == "invalid_request_auth"
            and event["endpoint"] == "/api/mcp"
            and _anonymous(event),
            [resp.status, event],
        )
        report.check(
            "D3. a call without a token counts towards the IP ban (+1)",
            after == before + 1,
            [before, after],
        )

        # ---- 3. Type 2: a token core rejected ------------------------------
        await _mcp(session, "Probe/expired", f"Bearer {admin_expired}")
        await _mcp(session, "Probe/llat-expired", f"Bearer {llat_expired}")
        await _mcp(session, "Probe/forged", f"Bearer {forged}")
        await _mcp(session, "Probe/unsigned", f"Bearer {unsigned}")
        await _mcp(session, "Probe/unknown-issuer", f"Bearer {unknown_issuer}")
        await _mcp(session, "Probe/revoked", f"Bearer {revoked_valid}")
        await _mcp(session, "Probe/garbage", "Bearer not-a-token")
        await _mcp(session, "Probe/leaver", f"Bearer {leaver_valid}")
        await _mcp(session, "Probe/homebody", f"Bearer {homebody_valid}", OUTSIDE)
        await _mcp(session, "Probe/basic", "Basic cHJvYmU6cHJvYmU=")
        await hass.async_block_till_done()

        event = _event("Probe/expired")
        report.check(
            "D4. expired token: expired_token with user, username, client_id, "
            "token type normal",
            event is not None
            and event["rejection"] == "expired_token"
            and _attributed(event, admin, client_id=CLIENT_ID, token_type="normal"),
            event,
        )
        event = _event("Probe/llat-expired")
        report.check(
            "D5. expired long-lived token: token type and client_name",
            event is not None
            and event["rejection"] == "expired_token"
            and _attributed(
                event,
                admin,
                token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN,
                client_name=LLAT_NAME,
            ),
            event,
        )
        for number, agent, what in (
            (6, "Probe/forged", "forged signature on a real refresh token ID"),
            (7, "Probe/unsigned", "unsigned token (alg none) naming a real user"),
            (8, "Probe/unknown-issuer", "token naming a refresh token that never was"),
            (9, "Probe/revoked", "token of a revoked refresh token"),
            (10, "Probe/garbage", "a bearer value that is not a JWT"),
            (11, "Probe/leaver", "token of a user deactivated since"),
        ):
            event = _event(agent)
            report.check(
                f"D{number}. {what}: unknown_or_revoked_token, nobody named",
                event is not None
                and event["rejection"] == "unknown_or_revoked_token"
                and _anonymous(event),
                event,
            )
        event = _event("Probe/homebody")
        report.check(
            "D12. valid token of a local-only user from outside: user_not_allowed "
            "with user",
            event is not None
            and event["rejection"] == "user_not_allowed"
            and event["ip_address"] == OUTSIDE
            and _attributed(event, homebody, client_id=CLIENT_ID, token_type="normal"),
            event,
        )
        event = _event("Probe/basic")
        report.check(
            "D13. Authorization header that is not Bearer: malformed_credentials",
            event is not None
            and event["rejection"] == "malformed_credentials"
            and _anonymous(event),
            event,
        )

        # ---- 4. Type 3: a valid user the endpoint turns away -----------------
        member_resp = await _mcp(session, "Probe/member", f"Bearer {member_valid}")
        admin_resp = await _mcp(session, "Probe/admin", f"Bearer {admin_valid}")
        await hass.async_block_till_done()
        event = _event("Probe/member")
        report.check(
            "D14. non-admin on an MCP server that requires admin: 401, not_admin "
            "with user and client",
            member_resp.status == 401
            and event is not None
            and event["rejection"] == "not_admin"
            and _attributed(event, member, client_id=CLIENT_ID, token_type="normal"),
            [member_resp.status, event],
        )
        report.check(
            "D15. the admin with a valid token is let through and not recorded",
            admin_resp.status != 401 and _event("Probe/admin") is None,
            admin_resp.status,
        )

        # ---- 5. The same over the websocket -----------------------------------
        results = [
            await _ws_auth(session, "Probe/ws-expired", {"access_token": llat_expired}),
            await _ws_auth(session, "Probe/ws-forged", {"access_token": forged}),
            await _ws_auth(session, "Probe/ws-none", {}),
            await _ws_auth(session, "Probe/ws-password", {"api_password": "probe"}),
        ]
        await hass.async_block_till_done()
        ws = {
            agent: _event(agent)
            for agent in (
                "Probe/ws-expired",
                "Probe/ws-forged",
                "Probe/ws-none",
                "Probe/ws-password",
            )
        }
        report.check(
            "D16. websocket: expired long-lived token is attributed, forged is not",
            results[:2] == ["auth_invalid", "auth_invalid"]
            and ws["Probe/ws-expired"] is not None
            and ws["Probe/ws-expired"]["endpoint"] == "/api/websocket"
            and ws["Probe/ws-expired"]["rejection"] == "expired_token"
            and _attributed(
                ws["Probe/ws-expired"],
                admin,
                token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN,
                client_name=LLAT_NAME,
            )
            and ws["Probe/ws-forged"] is not None
            and ws["Probe/ws-forged"]["rejection"] == "unknown_or_revoked_token"
            and _anonymous(ws["Probe/ws-forged"]),
            [results, ws["Probe/ws-expired"], ws["Probe/ws-forged"]],
        )
        report.check(
            "D17. websocket: no token is no_credentials, a password is "
            "malformed_credentials",
            ws["Probe/ws-none"] is not None
            and ws["Probe/ws-none"]["rejection"] == "no_credentials"
            and ws["Probe/ws-password"] is not None
            and ws["Probe/ws-password"]["rejection"] == "malformed_credentials",
            [results[2:], ws["Probe/ws-none"], ws["Probe/ws-password"]],
        )

        # ---- 6. Other places core rejects -----------------------------------
        async with session.post(
            f"{BASE}/auth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": "f" * 128,
                "client_id": CLIENT_ID,
            },
            headers={"User-Agent": "Probe/token-grant"},
        ) as resp:
            grant_status = resp.status
        async with session.get(
            f"{BASE}/api/?authSig=probe.not.signed",
            headers={"User-Agent": "Probe/signed-path"},
        ) as resp:
            signed_status = resp.status
        await hass.async_block_till_done()
        event = _event("Probe/token-grant")
        report.check(
            "D18. rejected refresh grant on /auth/token: invalid_auth_request",
            grant_status == 400
            and event is not None
            and event["rejection"] == "invalid_auth_request"
            and event["endpoint"] == "/auth/token"
            and _anonymous(event),
            [grant_status, event],
        )
        event = _event("Probe/signed-path")
        report.check(
            "D19. a bad authSig signed path: invalid_signed_path",
            signed_status == 401
            and event is not None
            and event["rejection"] == "invalid_signed_path",
            [signed_status, event],
        )

        # ---- 7. Folding keeps the reasons and clients apart ------------------
        await _mcp(session, "Probe/no-credentials-2")
        await hass.async_block_till_done()
        on_mcp = [
            h
            for h in data.history
            if h["result"] == "invalid_request_auth" and h["endpoint"] == "/api/mcp"
        ]
        reasons = sorted(
            (h["reason"], h.get("user_id") or "", h["count"]) for h in on_mcp
        )
        report.check(
            "D20. history on /api/mcp: one entry per reason and user, repeats folded",
            reasons
            == sorted(
                [
                    ("no_credentials", "", 2),
                    # The admin's normal and long-lived token: two clients.
                    ("expired_token", admin.id, 1),
                    ("expired_token", admin.id, 1),
                    ("unknown_or_revoked_token", "", 6),
                    ("user_not_allowed", homebody.id, 1),
                    ("malformed_credentials", "", 1),
                    ("not_admin", member.id, 1),
                ]
            ),
            reasons,
        )
        expired_entry = next(
            (h for h in on_mcp if h["reason"] == "expired_token"), None
        )
        report.check(
            "D21. the stored entry has username, client and token type",
            expired_entry is not None
            and expired_entry["username"] == "probe-admin"
            and expired_entry["token_type"] is not None
            and "client_id" in expired_entry
            and "client_name" in expired_entry,
            expired_entry,
        )

        # ---- 8. The websocket command ----------------------------------------
        responses = await _ws(
            session,
            admin_valid,
            [
                {"type": "login_detail/sign_in_log/list", "reason": "not_admin"},
                {"type": "login_detail/sign_in_log/list", "user_id": member.id},
            ],
        )
        by_reason = responses[0].get("result", {}).get("entries", [])
        by_user = responses[1].get("result", {}).get("entries", [])
        report.check(
            "D22. sign_in_log/list: reason filter, client fields, user filter "
            "finds the rejection",
            len(by_reason) == 1
            and by_reason[0]["reason"] == "not_admin"
            and by_reason[0]["client_id"] == CLIENT_ID
            and by_reason[0]["token_type"] == "normal"
            and by_reason[0]["client_name"] is None
            and any(e["type"] == "auth.request_rejected" for e in by_user),
            [by_reason, by_user],
        )

        # ---- 9. The token is nowhere -------------------------------------
        await data.async_flush()
        stored = await hass.async_add_executor_job(
            _read, hass.config.path(".storage", "login_detail_history")
        )
        places = {
            "history": json.dumps(data.history),
            "storage file": stored,
            "events": json.dumps(events),
            "log": "\n".join(capture.lines),
            "websocket": json.dumps(responses),
        }
        leaks = [
            place
            for place, text in places.items()
            for token in secrets
            for piece in _pieces(token)
            if piece in text
        ]
        report.check(
            "D23. no token, signature or payload in history, storage, events, "
            "websocket or the log",
            not leaks,
            sorted(set(leaks)),
        )
        sign_in_lines = [line for line in capture.lines if "Sign-in log:" in line]
        report.check(
            "D24. the log line names the reason and client of a proven token",
            any(
                "reason=not_admin" in line
                and "username=probe-member" in line
                and f"client_id={CLIENT_ID}" in line
                for line in sign_in_lines
            )
            and any("reason=no_credentials" in line for line in sign_in_lines),
            sign_in_lines[-3:],
        )

        # ---- 10. Consequence: an MCP client can get its own IP banned --------
        threshold = app[KEY_LOGIN_THRESHOLD]
        app[KEY_FAILED_LOGIN_ATTEMPTS][LOCALHOST] = threshold - 1
        last = await _mcp(session, "Probe/no-credentials-ban")
        then = await _mcp(session, "Probe/after-ban", f"Bearer {admin_valid}")
        report.check(
            "D25. with the counter at threshold - 1, one call without a token bans "
            "the IP; the next, valid call gets 403",
            last.status == 401 and then.status == 403,
            [threshold, last.status, then.status],
        )
    finally:
        await session.close()
        logging.getLogger().removeHandler(capture)
