"""Acceptance test probe F - /auth/token details and ingress (0.10.0).

Like the other probes this is NOT part of login_detail and is never installed
on a real instance. It sets the integration up from the UI and sends real
HTTP and websocket requests: refresh grants on /auth/token with a revoked
token, the wrong client ID, an invalid client ID and no token, an
authorization code grant with a made-up code, an unknown grant type, expired
and deleted tokens, a bearer value that is not a JWT, and requests through a
proxy with X-Forwarded-For and a different Host header. It then switches the
features off as the self-check would, checks the fallback, and looks for every
token and secret it used in the history, the storage file, the events, the
websocket and the whole log. Writes report6.json.

It needs the same `http` setup as probe D: `ip_ban_enabled: true`, a high
`login_attempts_threshold`, `use_x_forwarded_for: true` and
`trusted_proxies: [127.0.0.1]`.

If the environment variable LOGIN_DETAIL_PROBE_SECRETS names a file outside
the config directory, every secret is written there, one per line, so the log
and the config directory can be searched for them afterwards with grep. The
events are written to events6.json in the config directory for the same
reason.

All users, clients and tokens below are invented and exist only in the
throwaway config.
"""

from __future__ import annotations

from datetime import timedelta
import json
import logging
import os
import time
import traceback
from typing import Any

import aiohttp
from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.typing import ConfigType
import jwt

DOMAIN = "login_detail_probe6"
TARGET = "login_detail"
REPORT = "report6.json"
EVENTS = "events6.json"
EVENT_REJECTED = "login_detail_request_rejected"
EVENT_FAILED = "login_detail_failed"

BASE = "http://127.0.0.1:18123"
PASSWORD = "correct-horse-battery"
CLIENT_ID = "https://app.example/"
OTHER_CLIENT_ID = "https://other-app.example/"
LLAT_NAME = "Probe music server"
# Outside the local network (TEST-NET-2 and -3), sent through the trusted
# proxy 127.0.0.1 as X-Forwarded-For.
OUTSIDE = "203.0.113.7"
PROXIED = "198.51.100.23"
PROXY_HOST = "ha.proxy.example:8443"
WRONG_PASSWORD = "wrong-password-5e2a"

_LOGGER = logging.getLogger(__name__)


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def check(self, name: str, ok: bool, detail: Any = None) -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)


class LogCapture(logging.Handler):
    """Collects every log record of the instance, not just login_detail's."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def lines(self) -> list[str]:
        return [record.getMessage() for record in self.records]

    def full_text(self) -> str:
        """Every message, with tracebacks, as a log file would have it."""
        formatter = logging.Formatter("%(levelname)s %(name)s %(message)s")
        return "\n".join(formatter.format(record) for record in self.records)


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
    _LOGGER.warning("LOGIN-DETAIL-PROBE6 %s/%s", passed, len(report.checks))
    hass.async_create_task(hass.async_stop())


def _write(path: str, payload: Any) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)


def _write_lines(path: str, lines: list[str]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


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


def _expired(refresh: Any) -> str:
    now = int(time.time())
    return jwt.encode(
        {"iss": refresh.id, "iat": now - 3600, "exp": now - 1800},
        refresh.jwt_key,
        algorithm="HS256",
    )


def _pieces(secret: str) -> list[str]:
    """The secret and every part of a JWT that must not leak."""
    return [secret, *(part for part in secret.split(".") if len(part) >= 8)]


def _anonymous(event: dict[str, Any] | None) -> bool:
    return event is not None and all(
        event.get(key) is None
        for key in ("user_id", "username", "client_name", "token_type")
    )


async def _checks(hass: HomeAssistant, report: Report) -> None:
    capture = LogCapture()
    logging.getLogger().addHandler(capture)
    logging.getLogger("custom_components.login_detail").setLevel(logging.DEBUG)

    events: list[dict[str, Any]] = []

    def _on_event(event: Event) -> None:
        events.append({"event_type": event.event_type, **event.data})

    hass.bus.async_listen(EVENT_REJECTED, _on_event)
    hass.bus.async_listen(EVENT_FAILED, _on_event)

    def _event(agent: str) -> dict[str, Any] | None:
        found = [e for e in events if e.get("user_agent") == agent]
        return found[-1] if len(found) == 1 else None

    # ---- 1. Setup and self-check ---------------------------------------------
    flow = await hass.config_entries.flow.async_init(TARGET, context={"source": "user"})
    if flow.get("type") == "form":
        await hass.config_entries.flow.async_configure(flow["flow_id"], {})
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(TARGET)[0]
    data = hass.data[TARGET]
    self_check = [line for line in capture.lines if "self-check" in line]
    report.check(
        "F1. loaded, and the self-check passed for token identification and "
        "/auth/token on 2026.10.1",
        entry.state is ConfigEntryState.LOADED
        and any(
            "self-check passed: token_endpoint, token_identification" in line
            for line in self_check
        ),
        [str(entry.state), self_check],
    )

    admin = await _user(hass, "probe-admin", "system-admin")
    homebody = await _user(hass, "probe-homebody", "system-admin")
    await hass.auth.async_update_user(homebody, local_only=True)
    goner = await _user(hass, "probe-goner", "system-users")

    llat_refresh = await hass.auth.async_create_refresh_token(
        admin,
        client_name=LLAT_NAME,
        token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN,
        access_token_expiration=timedelta(days=3650),
    )
    app_refresh = await hass.auth.async_create_refresh_token(admin, CLIENT_ID)
    revoked_refresh = await hass.auth.async_create_refresh_token(admin, CLIENT_ID)
    homebody_refresh = await hass.auth.async_create_refresh_token(homebody, CLIENT_ID)
    deleted_refresh = await hass.auth.async_create_refresh_token(admin, CLIENT_ID)
    goner_refresh = await hass.auth.async_create_refresh_token(goner, CLIENT_ID)

    llat_expired = _expired(llat_refresh)
    admin_valid = hass.auth.async_create_access_token(app_refresh)
    deleted_valid = hass.auth.async_create_access_token(deleted_refresh)
    goner_valid = hass.auth.async_create_access_token(goner_refresh)
    revoked_value = revoked_refresh.token
    garbage = "rAnd0m-n0t-a-jwt-6f1d2b8c9e"
    made_up_code = "probe-made-up-authorization-code-7c41"
    secrets = [
        llat_expired,
        admin_valid,
        deleted_valid,
        goner_valid,
        revoked_value,
        app_refresh.token,
        homebody_refresh.token,
        garbage,
        made_up_code,
        PASSWORD,
        WRONG_PASSWORD,
    ]
    # The refresh tokens still alive at the end (app_refresh, homebody_refresh,
    # llat_refresh) are in core's own .storage/auth, as for every session.
    core_owned = [app_refresh.token, homebody_refresh.token]

    hass.auth.async_remove_refresh_token(revoked_refresh)
    hass.auth.async_remove_refresh_token(deleted_refresh)
    await hass.auth.async_remove_user(goner)

    session = aiohttp.ClientSession()
    try:
        # ---- 2. Acceptance 1: expired long-lived token on the websocket ------
        async with session.ws_connect(
            f"{BASE}/api/websocket", headers={"User-Agent": "Probe/F-ws-llat"}
        ) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": llat_expired})
            ws_result = (await ws.receive_json()).get("type")
        await hass.async_block_till_done()
        event = _event("Probe/F-ws-llat")
        stored = next(
            (h for h in data.history if h.get("user_agent") == "Probe/F-ws-llat"), None
        )
        report.check(
            "F2. expired long-lived token on /api/websocket: event names user, "
            "token name, user agent and endpoint",
            ws_result == "auth_invalid"
            and event is not None
            and event["endpoint"] == "/api/websocket"
            and event["rejection"] == "expired_token"
            and event["username"] == "probe-admin"
            and event["client_name"] == LLAT_NAME
            and event["token_type"] == TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
            and event["ingress"] == "direct",
            event,
        )
        report.check(
            "F3. ... and so does the history entry",
            stored is not None
            and stored["endpoint"] == "/api/websocket"
            and stored["username"] == "probe-admin"
            and stored["client_name"] == LLAT_NAME
            and stored["user_agent"] == "Probe/F-ws-llat",
            stored,
        )

        # ---- 3. Acceptance 2 and the other /auth/token reasons ---------------
        async def _grant(
            agent: str, form: dict[str, str], forwarded_for: str | None = None
        ) -> int:
            headers = {"User-Agent": agent}
            if forwarded_for:
                headers["X-Forwarded-For"] = forwarded_for
            async with session.post(
                f"{BASE}/auth/token", data=form, headers=headers
            ) as resp:
                await resp.read()
                return resp.status

        statuses = {
            "revoked": await _grant(
                "Probe/F-revoked",
                {
                    "grant_type": "refresh_token",
                    "refresh_token": revoked_value,
                    "client_id": CLIENT_ID,
                },
            ),
            "mismatch": await _grant(
                "Probe/F-mismatch",
                {
                    "grant_type": "refresh_token",
                    "refresh_token": app_refresh.token,
                    "client_id": OTHER_CLIENT_ID,
                },
            ),
            "homebody": await _grant(
                "Probe/F-homebody",
                {
                    "grant_type": "refresh_token",
                    "refresh_token": homebody_refresh.token,
                    "client_id": CLIENT_ID,
                },
                OUTSIDE,
            ),
            "bad-client": await _grant(
                "Probe/F-bad-client",
                {
                    "grant_type": "refresh_token",
                    "refresh_token": revoked_value,
                    "client_id": "not a url",
                },
            ),
            "no-token": await _grant(
                "Probe/F-no-token",
                {"grant_type": "refresh_token", "client_id": CLIENT_ID},
            ),
            "code": await _grant(
                "Probe/F-code",
                {
                    "grant_type": "authorization_code",
                    "code": made_up_code,
                    "client_id": CLIENT_ID,
                },
            ),
            "grant": await _grant(
                "Probe/F-grant",
                {"grant_type": "password", "username": "probe-admin"},
            ),
        }
        await hass.async_block_till_done()

        event = _event("Probe/F-revoked")
        report.check(
            "F4. revoked refresh token on /auth/token: grant_type, client_id and "
            "unknown_or_revoked_token, nobody named",
            statuses["revoked"] == 400
            and event is not None
            and event["endpoint"] == "/auth/token"
            and event["grant_type"] == "refresh_token"
            and event["client_id"] == CLIENT_ID
            and event["rejection"] == "unknown_or_revoked_token"
            and _anonymous(event),
            [statuses["revoked"], event],
        )
        event = _event("Probe/F-mismatch")
        report.check(
            "F5. real refresh token with another client_id: client_id_mismatch, "
            "the requested client_id, and the token's user",
            statuses["mismatch"] == 400
            and event is not None
            and event["rejection"] == "client_id_mismatch"
            and event["client_id"] == OTHER_CLIENT_ID
            and event["user_id"] == admin.id
            and event["token_type"] == "normal",
            [statuses["mismatch"], event],
        )
        event = _event("Probe/F-homebody")
        report.check(
            "F6. local-only user's refresh token from outside: user_not_allowed "
            "with user",
            statuses["homebody"] == 403
            and event is not None
            and event["rejection"] == "user_not_allowed"
            and event["user_id"] == homebody.id
            and event["ip_address"] == OUTSIDE,
            [statuses["homebody"], event],
        )
        event = _event("Probe/F-bad-client")
        report.check(
            "F7. client_id that is not a URL: invalid_client_id, the client_id is "
            "not stored",
            statuses["bad-client"] == 400
            and event is not None
            and event["rejection"] == "invalid_client_id"
            and event["client_id"] is None
            and _anonymous(event),
            [statuses["bad-client"], event],
        )
        event = _event("Probe/F-no-token")
        report.check(
            "F8. refresh grant without a refresh token: malformed_credentials",
            statuses["no-token"] == 400
            and event is not None
            and event["rejection"] == "malformed_credentials"
            and event["grant_type"] == "refresh_token",
            [statuses["no-token"], event],
        )
        event = _event("Probe/F-code")
        report.check(
            "F9. made-up authorization code: invalid_authorization_code",
            statuses["code"] == 400
            and event is not None
            and event["rejection"] == "invalid_authorization_code"
            and event["grant_type"] == "authorization_code"
            and event["client_id"] == CLIENT_ID
            and _anonymous(event),
            [statuses["code"], event],
        )
        event = _event("Probe/F-grant")
        report.check(
            "F10. unknown grant type: unsupported_grant_type, stored as 'other' "
            "and not as typed",
            statuses["grant"] == 400
            and event is not None
            and event["rejection"] == "unsupported_grant_type"
            and event["grant_type"] == "other",
            [statuses["grant"], event],
        )

        # ---- 4. Acceptance 3: deleted tokens and garbage ------------------------
        async def _api(agent: str, token: str) -> int:
            async with session.get(
                f"{BASE}/api/config",
                headers={"User-Agent": agent, "Authorization": f"Bearer {token}"},
            ) as resp:
                await resp.read()
                return resp.status

        api_statuses = [
            await _api("Probe/F-deleted-refresh", deleted_valid),
            await _api("Probe/F-deleted-user", goner_valid),
            await _api("Probe/F-garbage", garbage),
        ]
        async with session.ws_connect(
            f"{BASE}/api/websocket", headers={"User-Agent": "Probe/F-ws-garbage"}
        ) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": garbage})
            await ws.receive_json()
        await hass.async_block_till_done()
        unknown = [_event("Probe/F-deleted-refresh"), _event("Probe/F-deleted-user")]
        report.check(
            "F11. token of a deleted refresh token and of a deleted user: "
            "unknown_or_revoked_token, nobody named",
            api_statuses[:2] == [401, 401]
            and all(
                e is not None
                and e["rejection"] == "unknown_or_revoked_token"
                and _anonymous(e)
                for e in unknown
            ),
            [api_statuses, unknown],
        )
        malformed = [_event("Probe/F-garbage"), _event("Probe/F-ws-garbage")]
        errors = [
            record.getMessage()
            for record in capture.records
            if (record.exc_info or record.levelno >= logging.ERROR)
            and record.name.startswith(
                (
                    "custom_components.login_detail",
                    "homeassistant.components.http",
                    "homeassistant.components.websocket_api",
                    "homeassistant.components.auth",
                    "aiohttp",
                )
            )
        ]
        report.check(
            "F12. random string as bearer, REST and websocket: "
            "malformed_credentials, and no exception or error in the log",
            api_statuses[2] == 401
            and all(
                e is not None and e["rejection"] == "malformed_credentials"
                for e in malformed
            )
            and not errors,
            [malformed, errors],
        )

        # ---- 5. Acceptance 4: through a proxy ----------------------------------
        async with session.get(
            f"{BASE}/api/config",
            headers={
                "User-Agent": "Probe/F-proxy",
                "Host": PROXY_HOST,
                "X-Forwarded-For": f"192.0.2.250, {PROXIED}",
            },
        ) as resp:
            proxy_status = resp.status
        await hass.async_block_till_done()
        event = _event("Probe/F-proxy")
        stored = next(
            (h for h in data.history if h.get("user_agent") == "Probe/F-proxy"), None
        )
        report.check(
            "F13. through a proxy: Host and the raw X-Forwarded-For chain are "
            "recorded, the latter as untrusted, in the event and the history",
            proxy_status == 401
            and event is not None
            and event["host"] == PROXY_HOST
            and event["x_forwarded_for_untrusted"] == f"192.0.2.250, {PROXIED}"
            and event["ip_address"] == PROXIED
            and event["ingress"] == "direct"
            and "x_forwarded_for" not in event
            and stored is not None
            and stored["host"] == PROXY_HOST
            and stored["x_forwarded_for_untrusted"] == f"192.0.2.250, {PROXIED}",
            [proxy_status, event, stored],
        )

        # A failed login in the login flow gets the same fields.
        async with session.post(
            f"{BASE}/auth/login_flow",
            json={
                "client_id": f"{BASE}/",
                "handler": ["homeassistant", None],
                "redirect_uri": f"{BASE}/?auth_callback=1",
            },
            headers={"User-Agent": "Probe/F-login", "X-Forwarded-For": OUTSIDE},
        ) as resp:
            flow_id = (await resp.json()).get("flow_id")
        async with session.post(
            f"{BASE}/auth/login_flow/{flow_id}",
            json={
                "client_id": f"{BASE}/",
                "username": "probe-admin",
                "password": WRONG_PASSWORD,
            },
            headers={
                "User-Agent": "Probe/F-login",
                "Host": PROXY_HOST,
                "X-Forwarded-For": OUTSIDE,
            },
        ) as resp:
            await resp.read()
        await hass.async_block_till_done()
        event = _event("Probe/F-login")
        report.check(
            "F14. failed login in the login flow: username as before, plus "
            "ingress, Host and X-Forwarded-For (untrusted)",
            event is not None
            and event["event_type"] == EVENT_FAILED
            and event["reason"] == "invalid_auth"
            and event["username"] == "probe-admin"
            and event["endpoint"] == "/auth/login_flow"
            and event["ingress"] == "direct"
            and event["host"] == PROXY_HOST
            and event["x_forwarded_for_untrusted"] == OUTSIDE,
            event,
        )

        # ---- 6. Websocket command, log line, panel ---------------------------
        async with session.ws_connect(f"{BASE}/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": admin_valid})
            await ws.receive_json()
            await ws.send_json(
                {"id": 1, "type": "login_detail/sign_in_log/list", "limit": 100}
            )
            listed = await ws.receive_json()
        entries = listed.get("result", {}).get("entries", [])
        proxied = next((e for e in entries if e.get("host") == PROXY_HOST), None)
        revoked = next((e for e in entries if e.get("grant_type")), None)
        report.check(
            "F15. sign_in_log/list carries grant_type, ingress, host and "
            "x_forwarded_for_untrusted",
            proxied is not None
            and proxied["ingress"] == "direct"
            and proxied["x_forwarded_for_untrusted"]
            and revoked is not None,
            [proxied, revoked],
        )
        lines = [line for line in capture.lines if "Sign-in log:" in line]
        report.check(
            "F16. the log line has grant_type, ingress, host and "
            "x_forwarded_for_untrusted",
            any(
                "reason=unknown_or_revoked_token" in line
                and "grant_type=refresh_token" in line
                and f"client_id={CLIENT_ID}" in line
                for line in lines
            )
            and any(
                f"host='{PROXY_HOST}'" in line
                and "ingress=direct" in line
                and f"x_forwarded_for_untrusted='192.0.2.250, {PROXIED}'" in line
                for line in lines
            ),
            lines[-3:],
        )
        async with session.get(
            f"{BASE}/login_detail_static/sign-in-log-panel.js"
        ) as resp:
            panel = await resp.text()
        report.check(
            "F17. the panel labels X-Forwarded-For as untrusted",
            "X-Forwarded-For (untrusted)" in panel and "can be forged" in panel,
            resp.status,
        )

        # ---- 7. Fallback when the self-check fails ---------------------------
        rejection = __import__(
            "custom_components.login_detail.rejection", fromlist=["features"]
        )
        integration = __import__(
            "custom_components.login_detail", fromlist=["_async_self_check"]
        )
        original = rejection._token_endpoint_ok

        def _broken(hass: HomeAssistant) -> bool:
            raise AttributeError("core moved TokenView")

        rejection._token_endpoint_ok = _broken
        try:
            before = len(capture.records)
            await integration._async_self_check(hass)
            warned = [
                r.getMessage()
                for r in capture.records[before:]
                if r.levelno == logging.WARNING and "self-check" in r.getMessage()
            ]
            await _grant(
                "Probe/F-degraded-token",
                {
                    "grant_type": "refresh_token",
                    "refresh_token": revoked_value,
                    "client_id": CLIENT_ID,
                },
            )
            # The self-check above found token identification fine again;
            # switch it off by hand to see the other fallback.
            rejection.features[rejection.FEATURE_TOKEN_IDENTIFICATION] = False
            await _api("Probe/F-degraded-jwt", deleted_valid)
            await hass.async_block_till_done()
        finally:
            rejection._token_endpoint_ok = original
            await integration._async_self_check(hass)
        degraded_token = _event("Probe/F-degraded-token")
        degraded_jwt = _event("Probe/F-degraded-jwt")
        report.check(
            "F18. a failed self-check warns and falls back: /auth/token is "
            "invalid_auth_request, a token gets no reason, nothing raises",
            any("token_endpoint does not match" in line for line in warned)
            and degraded_token is not None
            and degraded_token["rejection"] == "invalid_auth_request"
            and degraded_token["grant_type"] is None
            and degraded_jwt is not None
            and degraded_jwt["rejection"] is None
            and _anonymous(degraded_jwt)
            and all(rejection.features.values()),
            [warned, degraded_token, degraded_jwt, dict(rejection.features)],
        )

        # ---- 8. Acceptance 5: no secret anywhere ---------------------------------
        await data.async_flush()
        storage = await hass.async_add_executor_job(
            _read, hass.config.path(".storage", "login_detail_history")
        )
        places = {
            "history": json.dumps(data.history),
            "storage file": storage,
            "events": json.dumps(events),
            "log": capture.full_text(),
            "websocket": json.dumps(listed),
        }
        leaks = sorted(
            {
                place
                for place, text in places.items()
                for secret in secrets
                for piece in _pieces(secret)
                if piece in text
            }
        )
        report.check(
            "F19. no token, refresh token, code, password or random bearer value "
            "in history, storage, events, websocket or the log",
            not leaks,
            leaks,
        )
        await hass.async_add_executor_job(_write, hass.config.path(EVENTS), events)
        if path := os.environ.get("LOGIN_DETAIL_PROBE_SECRETS"):
            await hass.async_add_executor_job(
                _write_lines,
                path,
                [piece for secret in secrets for piece in _pieces(secret)],
            )
            await hass.async_add_executor_job(
                _write_lines, f"{path}.core_owned", core_owned
            )
    finally:
        await session.close()
        logging.getLogger().removeHandler(capture)
