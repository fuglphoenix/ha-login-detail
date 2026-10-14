"""The sign-in log over the websocket API - admin only.

    {"type": "login_detail/sign_in_log/list"}

returns the stored history as log entries, newest first, in the shape
proposed for core in home-assistant/architecture#1346:

    ts, type, reason, username, user_id, auth_provider, ip, hostname,
    endpoint, user_agent, flow_id, count

Optional filters, all combined with AND:

    log_type     auth.login_failed | auth.login_success | auth.request_rejected
    username     exact, case-insensitive
    user_id      exact
    ip           prefix - "192.0.2." matches the whole range
    start_time   ISO 8601, inclusive
    end_time     ISO 8601, exclusive
    limit        at most this many entries (the newest)

The log holds personal data, so the command is admin only: core's
`require_admin` answers anyone else with `unauthorized`. The command is the
one a core implementation would have (there as `auth/sign_in_log/list`), and
it reads nothing but the history the integration already keeps.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util
import probatio

from .const import (
    DOMAIN,
    LOG_TYPE_LOGIN_FAILED,
    LOG_TYPE_LOGIN_SUCCESS,
    LOG_TYPE_REQUEST_REJECTED,
    LOG_TYPES,
    MAX_ENTRIES,
    RESULT_OK,
    RESULT_REQUEST_REJECTED,
)


def log_type_of(result: str | None) -> str:
    """The log `type` of a stored `result`."""
    if result == RESULT_OK:
        return LOG_TYPE_LOGIN_SUCCESS
    if result == RESULT_REQUEST_REJECTED:
        return LOG_TYPE_REQUEST_REJECTED
    return LOG_TYPE_LOGIN_FAILED


def as_log_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """A stored history entry as a sign-in log entry.

    Entries stored before 0.7.0 have no user_id or user_agent; they come out
    as None.
    """
    result = entry.get("result")
    return {
        "ts": entry.get("timestamp"),
        "type": log_type_of(result),
        "reason": None if result == RESULT_OK else result,
        "username": entry.get("username"),
        "user_id": entry.get("user_id"),
        "auth_provider": entry.get("auth_provider"),
        "ip": entry.get("ip_address"),
        "hostname": entry.get("hostname"),
        "endpoint": entry.get("endpoint"),
        "user_agent": entry.get("user_agent"),
        "flow_id": entry.get("flow_id"),
        "count": int(entry.get("count") or 1),
    }


@callback
def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register the commands. Called once, from async_setup."""
    websocket_api.async_register_command(hass, ws_sign_in_log_list)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/sign_in_log/list",
        probatio.Optional("log_type"): probatio.In(LOG_TYPES),
        probatio.Optional("username"): str,
        probatio.Optional("user_id"): str,
        probatio.Optional("ip"): str,
        probatio.Optional("start_time"): str,
        probatio.Optional("end_time"): str,
        probatio.Optional("limit"): probatio.All(
            int, probatio.Range(min=1, max=MAX_ENTRIES)
        ),
    }
)
@callback
def ws_sign_in_log_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the sign-in log, filtered."""
    store = hass.data.get(DOMAIN)
    history = getattr(store, "history", None)
    if history is None:
        connection.send_error(
            msg["id"], "not_loaded", "Login Detail is not set up or not loaded"
        )
        return

    bounds = {}
    for key in ("start_time", "end_time"):
        if key not in msg:
            continue
        parsed = dt_util.parse_datetime(msg[key])
        if parsed is None:
            connection.send_error(
                msg["id"], "invalid_format", f"{key} is not an ISO 8601 date and time"
            )
            return
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
        bounds[key] = parsed

    username = msg.get("username", "").strip().casefold()
    entries = []
    for entry in history:
        log_entry = as_log_entry(entry)
        if "log_type" in msg and log_entry["type"] != msg["log_type"]:
            continue
        if username and (log_entry["username"] or "").casefold() != username:
            continue
        if "user_id" in msg and log_entry["user_id"] != msg["user_id"]:
            continue
        if "ip" in msg and not (log_entry["ip"] or "").startswith(msg["ip"].strip()):
            continue
        if bounds:
            seen = dt_util.parse_datetime(log_entry["ts"] or "")
            if seen is None:
                continue
            if "start_time" in bounds and seen < bounds["start_time"]:
                continue
            if "end_time" in bounds and seen >= bounds["end_time"]:
                continue
        entries.append(log_entry)

    entries.sort(key=lambda entry: entry["ts"] or "", reverse=True)
    total = len(entries)
    if "limit" in msg:
        entries = entries[: msg["limit"]]
    connection.send_result(
        msg["id"],
        {
            "entries": entries,
            "total": total,
            "max_entries": store.max_entries,
            "max_days": store.max_days,
        },
    )
