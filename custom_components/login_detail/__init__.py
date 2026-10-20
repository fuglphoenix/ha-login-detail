"""Login Detail - who tries to log in, when, and from which IP address.

On a failed login attempt Home Assistant logs only the IP address and URL,
never the attempted username, and a *successful* login is not logged at all.
The former is a known, open gap in core: home-assistant/core#148110.

This integration monkeypatches the login flow and fires two events:

  login_detail_failed      username, user_id, ip_address, reason, flow_id,
                           auth_provider, endpoint, user_agent, ingress, host,
                           x_forwarded_for_untrusted
  login_detail_succeeded   username, user_id, ip_address, flow_id,
                           auth_provider, endpoint, user_agent, ingress, host,
                           x_forwarded_for_untrusted

It also wraps core's `process_wrong_login`, which handles every request that
is rejected for invalid authentication - a wrong or expired token on
/api/mcp, /api/websocket, /auth/token and so on. Those are not login flows and
carry no username, so they get their own event:

  login_detail_request_rejected   ip_address, endpoint, reason, rejection,
                                  user_id, username, client_id, client_name,
                                  token_type, grant_type, user_agent, ingress,
                                  host, x_forwarded_for_untrusted
                                  (+ attempts, threshold - and flow_id from
                                  the login flow - with reason ip_banned_now)

`reason` is `invalid_request_auth`; `rejection` says why (0.7.1,
see rejection.py). The user and client are only filled in when the token
behind the request has been proven to be theirs - the token itself is never
kept. On /auth/token, `grant_type` and the requested `client_id` are read
from the form core has already parsed (0.10.0).

The rejection that got the IP address banned (0.11.0): after core's
`process_wrong_login` has counted the attempt, its ban list is read (see
ban_status.py - read only). If this very call banned the address, the event
has `reason: ip_banned_now`, `attempts` and `threshold`, it is logged at
WARNING, and the history entry gets `ip_banned_now: true` and is never folded.
A wrong password or 2FA code goes through login_flow.py's own copy of
`process_wrong_login`. It is wrapped as well, but only to read the ban list:
the attempt is already reported as `login_detail_failed`, so the wrapper only
fires the `ip_banned_now` event and marks the flow's entry.

How the request came in (0.10.0): `ingress` is `nabu_casa` for a request
through Home Assistant Cloud remote access and `direct` otherwise, `host` is
the Host header, and `x_forwarded_for_untrusted` is the raw X-Forwarded-For
header as the client sent it. The client can write anything into that
header - it is named untrusted wherever it is shown, and `ip_address` stays
the address Home Assistant itself resolved.

Every event is also written as one line to the log - failed login attempts at
WARNING, everything else at INFO - stored in `.storage/login_detail_history`
and exposed as sensors (see sensor.py), so the history survives a restart.

Passwords and TOTP codes are never read, logged or forwarded. The username
of a failed attempt is only kept if it belongs to an existing user: people
type their password into the username field, and it must not end up in the
log, the events or the storage file.

The option `store_unknown_usernames` (0.9.0, off by default) lifts that for
the owner who wants to see what a brute force attempt tries: the username is
then kept as typed - cut to 64 characters, control characters replaced - and
the event and the entry carry `known_user: false`. With the option off,
nothing changes.

Sign-in log (0.7.0)
-------------------
The history is also offered as a sign-in log, in the shape proposed for core
in home-assistant/architecture#1346: an admin-only websocket command
(`login_detail/sign_in_log/list`, see websocket.py) and an admin-only side
panel ("Sign-in log", see frontend/). It is laid out the way a core
implementation would be - capture, storage, websocket API, page - so the
backend can move into core more or less as is. The log holds personal data
and is bounded by count and by age (options `max_entries` and `max_days`).

Setup
-----
The integration is set up from the UI: Settings -> Devices & services ->
Add integration -> Login Detail. The options set the retention of the log,
whether the side panel is shown, and whether unknown usernames are kept.

A `login_detail:` line in configuration.yaml is no longer needed. If it is
still there, `async_setup` imports it once into a config entry
(`async_step_import`), after which the line can be removed.

What `ok` means
---------------
`ok` is only reported once the login flow has *finished* - that is, after
2FA if the user has 2FA enabled. That is why success is reported from
`AuthManagerFlowManager.async_finish_flow` and not from `LoginFlow.async_finish`:
core calls `async_finish` as soon as the password has been accepted, and then
interrupts the flow in `async_finish_flow` to require 2FA. A hook on
`async_finish` would therefore see the same login twice, the first time before
2FA (the bug fixed in 0.3.0, see CHANGELOG.md).

The intermediate state is reported as `mfa_pending` instead (password correct,
2FA missing). When the flow completes, the `ok` entry replaces the pending
entry with the same flow_id, so one login gives one entry. If 2FA is never
submitted, `mfa_pending` remains - and that is exactly the attempt worth
seeing.

Written against Home Assistant 2026.10.1. The monkeypatch targets private API
(`HassLoginFlow.async_step_init`, `LoginFlow.async_step_mfa`,
`AuthManagerFlowManager.async_finish_flow`, `HassAuthProvider.data`,
`AuthPhase.async_handle`, and `process_wrong_login` in
`homeassistant.components.http.ban`,
`homeassistant.components.websocket_api.auth` and
`homeassistant.components.auth.login_flow`; the ban list in ban.py is read)
and may stop working with any core upgrade. If patching fails, it is logged
at startup and login continues unchanged. The token format and the
/auth/token flow the rejection details rely on are checked at startup
(rejection.async_self_check); a feature whose core API has changed is
switched off with a WARNING instead of failing silently.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from pathlib import Path
import socket
from typing import Any

from homeassistant.auth import AuthManagerFlowManager
from homeassistant.auth.providers import LoginFlow
from homeassistant.auth.providers.homeassistant import HassLoginFlow
from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig, ban as http_ban
from homeassistant.components.websocket_api import auth as websocket_auth
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.http import current_request
from homeassistant.helpers.network import is_cloud_connection
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .ban_status import BanState, ban_state
from .const import (
    CONF_MAX_DAYS,
    CONF_MAX_ENTRIES,
    CONF_SHOW_PANEL,
    CONF_STORE_UNKNOWN_USERNAMES,
    DEFAULT_MAX_DAYS,
    DEFAULT_MAX_ENTRIES,
    DEFAULT_SHOW_PANEL,
    DEFAULT_STORE_UNKNOWN_USERNAMES,
    DOMAIN,
    ENDPOINT_LOGIN_FLOW,
    ENDPOINT_TOKEN,
    EVENT_LOGIN_FAILED,
    EVENT_LOGIN_SUCCEEDED,
    EVENT_REQUEST_REJECTED,
    INGRESS_DIRECT,
    INGRESS_NABU_CASA,
    PANEL_COMPONENT,
    PANEL_ICON,
    PANEL_STATIC_URL,
    PANEL_TITLE,
    PANEL_URL_PATH,
    PRUNE_INTERVAL,
    REJECTED_FOLD_WINDOW,
    REJECTED_INVALID_AUTH_REQUEST,
    RESULT_IP_BANNED_NOW,
    RESULT_MFA_PENDING,
    RESULT_OK,
    RESULT_REQUEST_REJECTED,
    SIGNAL_UPDATED,
    UNKNOWN_USERNAME_MAX_LENGTH,
    USER_AGENT_MAX_LENGTH,
)
from .rejection import (
    ATTRIBUTION_FIELDS,
    async_self_check,
    classify,
    clean,
    token_form,
    websocket_token,
)
from .websocket import async_register_websocket_commands, log_type_of

try:  # private core API - without it, a login flow ban is not marked
    from homeassistant.components.auth import login_flow as auth_login_flow
except ImportError:  # pragma: no cover - depends on the core version
    auth_login_flow = None

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

STORAGE_KEY = f"{DOMAIN}_history"
STORAGE_VERSION = 1

# Wait a little before writing the history to disk, so several attempts in a
# row become a single write.
SAVE_DELAY = 10

# An endpoint is stored as the route pattern, so this only bounds odd cases.
ENDPOINT_MAX_LENGTH = 200

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

FRONTEND_DIR = Path(__file__).parent / "frontend"
PANEL_MODULE = "sign-in-log-panel.js"


class LoginDetailData:
    """History of login events, persisted to disk."""

    def __init__(
        self,
        hass: HomeAssistant,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_days: int = DEFAULT_MAX_DAYS,
        store_unknown_usernames: bool = DEFAULT_STORE_UNKNOWN_USERNAMES,
    ) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.history: list[dict[str, Any]] = []
        self._host_cache: dict[str, str | None] = {}
        self.max_entries = max_entries
        self.max_days = max_days
        self.store_unknown_usernames = store_unknown_usernames

    async def async_load(self) -> None:
        """Read the history from disk. An unreadable file must not break startup."""
        try:
            stored = await self._store.async_load()
        except Exception:  # a corrupt file must not block setup
            _LOGGER.exception(
                "login_detail: could not read the history, starting empty"
            )
            stored = None
        if isinstance(stored, dict) and isinstance(stored.get("history"), list):
            self.history = stored["history"][: self.max_entries]
        if self._prune():
            self._async_save()

    def _prune(self) -> bool:
        """Drop entries beyond the retention limits. True if any were dropped.

        The age limit is checked on every entry, not just the tail: the list
        is newest first, but a folded rejection moves to the front with a new
        timestamp, and the file may have been edited by hand.
        """
        before = len(self.history)
        cutoff = dt_util.utcnow() - timedelta(days=self.max_days)
        kept = []
        for entry in self.history[: self.max_entries]:
            seen = dt_util.parse_datetime(entry.get("timestamp") or "")
            if seen is not None and seen >= cutoff:
                kept.append(entry)
        self.history = kept
        return len(kept) != before

    @callback
    def async_prune_tick(self, now: datetime | None = None) -> None:
        """Remove expired entries on a timer, so they go even when it is quiet."""
        if self._prune():
            self._async_save()
            async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    @callback
    def _async_save(self) -> None:
        self._store.async_delay_save(lambda: {"history": self.history}, SAVE_DELAY)

    async def async_flush(self) -> None:
        """Write pending changes now.

        Writes are normally delayed by SAVE_DELAY seconds. On a restart Home
        Assistant forces the final write itself, but on a reload or removal
        from the UI it does not - and the newest entries would then be lost
        when the history is read back in. async_save also cancels the delayed
        write.
        """
        await self._store.async_save({"history": self.history})

    @callback
    def async_record(self, entry: dict[str, Any]) -> None:
        """Put the event at the front of the history immediately.

        The insert is synchronous, so entries stay in the order they happened -
        a reverse DNS lookup must neither delay the login response nor swap two
        entries. The hostname is filled in afterwards.
        """
        self._async_supersede_pending(entry)
        ip_address = entry.get("ip_address")
        cached = ip_address in self._host_cache if ip_address else False
        entry["hostname"] = self._host_cache.get(ip_address) if cached else None
        self.history.insert(0, entry)
        del self.history[self.max_entries :]
        self._async_save()
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)
        if ip_address and not cached:
            self.hass.async_create_background_task(
                self._async_fill_hostname(entry),
                f"{DOMAIN}_hostname_{entry.get('flow_id') or ip_address}",
            )

    @callback
    def async_record_rejected(self, entry: dict[str, Any]) -> None:
        """Record a rejected request, folding repeats into one entry.

        A client with a stale token retries on its own, often every few
        seconds. Without folding, one such client would push every real login
        attempt out of the bounded history. A repeat from the same IP address
        on the same endpoint within REJECTED_FOLD_WINDOW raises the count of
        the existing entry and moves it to the front instead - but only if it
        was rejected for the same reason and is attributed to the same user
        and client, so an unauthenticated probe never hides a known user's
        expired token, or the other way round. The entry that got the address
        banned is never folded into, and nothing from after it is folded into
        anything before it, so that moment stays visible.
        """
        now = dt_util.parse_datetime(entry["timestamp"])
        for index, previous in enumerate(self.history):
            if previous.get(RESULT_IP_BANNED_NOW) and previous.get(
                "ip_address"
            ) == entry.get("ip_address"):
                # Nothing from after the ban is folded into what came before.
                break
            if previous.get("result") != RESULT_REQUEST_REJECTED or any(
                previous.get(key) != entry.get(key) for key in _FOLD_KEYS
            ):
                continue
            seen = dt_util.parse_datetime(previous.get("timestamp") or "")
            if now is None or seen is None or now - seen > REJECTED_FOLD_WINDOW:
                break
            del self.history[index]
            previous["timestamp"] = entry["timestamp"]
            previous["count"] = int(previous.get("count") or 1) + 1
            # The newest user agent wins - a fold is one IP on one endpoint
            # for one reason and one client, so it is usually the same anyway.
            previous["user_agent"] = entry.get("user_agent")
            for key in INGRESS_FIELDS:
                previous[key] = entry.get(key)
            self.history.insert(0, previous)
            self._async_save()
            async_dispatcher_send(self.hass, SIGNAL_UPDATED)
            return
        entry["count"] = 1
        self.async_record(entry)

    @callback
    def async_mark_banned(
        self, flow_id: str | None, ip_address: str | None, marker: dict[str, Any]
    ) -> bool:
        """Mark the failed login that got the IP address banned. True if found.

        login_flow.py counts a wrong password or 2FA code after the flow step
        has run, so the step's entry is already in the history: the newest
        failed entry of the same flow from the same address.
        """
        if not flow_id:
            return False
        for entry in self.history:
            if entry.get("flow_id") != flow_id:
                continue
            if entry.get("ip_address") != ip_address or entry.get("result") in (
                RESULT_OK,
                RESULT_MFA_PENDING,
            ):
                return False
            entry.update(marker)
            self._async_save()
            async_dispatcher_send(self.hass, SIGNAL_UPDATED)
            return True
        return False

    @callback
    def _async_supersede_pending(self, entry: dict[str, Any]) -> None:
        """A final result replaces the flow's own `mfa_pending` entry."""
        flow_id = entry.get("flow_id")
        if not flow_id or entry.get("result") == RESULT_MFA_PENDING:
            return
        for index in range(len(self.history) - 1, -1, -1):
            previous = self.history[index]
            if (
                previous.get("flow_id") == flow_id
                and previous.get("result") == RESULT_MFA_PENDING
            ):
                del self.history[index]

    async def _async_fill_hostname(self, entry: dict[str, Any]) -> None:
        """Look up the hostname afterwards and update the entry if there is one."""
        hostname = await self._async_hostname(entry.get("ip_address"))
        if hostname is None or entry.get("hostname") == hostname:
            return
        entry["hostname"] = hostname
        self._async_save()
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    async def _async_hostname(self, ip_address: str | None) -> str | None:
        """Reverse DNS with a cache. A lookup must never block the login path."""
        if not ip_address:
            return None
        if ip_address in self._host_cache:
            return self._host_cache[ip_address]
        hostname: str | None = None
        try:
            hostname = (
                await self.hass.async_add_executor_job(socket.gethostbyaddr, ip_address)
            )[0]
        except Exception:  # noqa: BLE001 - a missing PTR record is normal
            hostname = None
        if len(self._host_cache) > 128:
            self._host_cache.clear()
        self._host_cache[ip_address] = hostname
        return hostname


# What must match for a rejected request to be folded into an earlier one.
_FOLD_KEYS = ("ip_address", "endpoint", "reason", "user_id", "client_id")

# How a request came in (0.10.0), on every event and entry.
INGRESS_FIELDS = ("ingress", "host", "x_forwarded_for_untrusted")


def _report(
    hass: HomeAssistant,
    result: str,
    username: str | None,
    user_id: str | None,
    ip_address: Any,
    flow: Any,
    flow_id: str,
    *,
    known_user: bool = True,
) -> None:
    """Log, fire and store one login event. Must never raise.

    `known_user=False` marks a username no user has, stored only because the
    `store_unknown_usernames` option is on. Only then is the key added, so
    with the option off every event and entry looks exactly as before.
    """
    request = current_request.get()
    data = {
        "username": username,
        "user_id": user_id,
        "ip_address": str(ip_address) if ip_address is not None else None,
        "reason": result,
        "flow_id": flow_id,
        "auth_provider": _provider_of(flow),
        "endpoint": ENDPOINT_LOGIN_FLOW,
        "user_agent": _user_agent_of(request),
        **_ingress_of(hass, request),
    }
    if not known_user:
        data["known_user"] = False

    _log_line(result, data)
    if result == RESULT_OK:
        del data["reason"]
        hass.bus.async_fire(EVENT_LOGIN_SUCCEEDED, data)
    else:
        hass.bus.async_fire(EVENT_LOGIN_FAILED, data)

    # The monkeypatch sits on core classes and has no config entry to look up.
    # That is why the history lives in hass.data and not in entry.runtime_data.
    store = hass.data.get(DOMAIN)
    if not isinstance(store, LoginDetailData):
        return
    entry = {
        "timestamp": dt_util.utcnow().isoformat(),
        "result": result,
        "username": data["username"],
        "user_id": data["user_id"],
        "ip_address": data["ip_address"],
        "auth_provider": data["auth_provider"],
        "flow_id": flow_id,
        "endpoint": ENDPOINT_LOGIN_FLOW,
        "user_agent": data["user_agent"],
        **{key: data[key] for key in INGRESS_FIELDS},
    }
    if not known_user:
        entry["known_user"] = False
    store.async_record(entry)


def _log_line(result: str, data: dict[str, Any]) -> None:
    """One log line per event, with the fields of the sign-in log.

    A failed login attempt is a WARNING. Everything else - a login, a login
    waiting for 2FA, a rejected request (which core has already logged at
    WARNING with the full URL) - is INFO. The client fields are only set for
    a rejected request whose token has been proven to be the client's.

    A username no user has is free text, so it is quoted with %r and followed
    by `known_user=False`; a line without the flag is unchanged.

    The request that got the IP address banned (`reason` ip_banned_now) is a
    WARNING, and the line ends with `ip_banned_now=True attempts=N
    threshold=N`.
    """
    banned = data.get("reason") == RESULT_IP_BANNED_NOW
    level = (
        logging.INFO
        if result in (RESULT_OK, RESULT_MFA_PENDING, RESULT_REQUEST_REJECTED)
        and not banned
        else logging.WARNING
    )
    unknown = data.get("known_user") is False
    suffix = " known_user=False" if unknown else ""
    if banned:
        suffix += " ip_banned_now=True attempts=%s threshold=%s"
    _LOGGER.log(
        level,
        "Sign-in log: type=%s reason=%s username=%s user_id=%s auth_provider=%s "
        "client_id=%s token_type=%s client_name=%r grant_type=%s ip=%s "
        "endpoint=%s user_agent=%r flow_id=%s ingress=%s host=%r "
        "x_forwarded_for_untrusted=%r" + suffix,
        log_type_of(result),
        None if result == RESULT_OK else data.get("rejection") or result,
        repr(data.get("username")) if unknown else data.get("username"),
        data.get("user_id"),
        data.get("auth_provider"),
        data.get("client_id"),
        data.get("token_type"),
        data.get("client_name"),
        data.get("grant_type"),
        data.get("ip_address"),
        data.get("endpoint"),
        data.get("user_agent"),
        data.get("flow_id"),
        data.get("ingress"),
        data.get("host"),
        data.get("x_forwarded_for_untrusted"),
        *((data.get("attempts"), data.get("threshold")) if banned else ()),
    )


async def _async_report_rejected(
    hass: HomeAssistant,
    request: Any,
    *,
    websocket: bool = False,
    ban: BanState | None = None,
) -> None:
    """Log, fire and store one rejected request. Must never raise.

    The form of a /auth/token request is only held here, in memory, for the
    duration of the call - see rejection.token_form. `ban` is set when this
    request got the IP address banned.
    """
    endpoint = _endpoint_of(request)
    form = await token_form(request) if endpoint == ENDPOINT_TOKEN else None
    found = classify(hass, request, endpoint, websocket=websocket, form=form)
    del form
    data = {
        "ip_address": str(request.remote) if request.remote is not None else None,
        "endpoint": endpoint,
        "reason": RESULT_REQUEST_REJECTED,
        "rejection": found["reason"],
        **{key: found[key] for key in ATTRIBUTION_FIELDS},
        "grant_type": found["grant_type"],
        "user_agent": _user_agent_of(request),
        **_ingress_of(hass, request),
    }
    if ban is not None:
        data.update(_ban_fields(ban))
    _log_line(RESULT_REQUEST_REJECTED, data)
    hass.bus.async_fire(EVENT_REQUEST_REJECTED, data)

    store = hass.data.get(DOMAIN)
    if not isinstance(store, LoginDetailData):
        return
    entry = {
        "timestamp": dt_util.utcnow().isoformat(),
        "result": RESULT_REQUEST_REJECTED,
        "reason": data["rejection"],
        "username": data["username"],
        "user_id": data["user_id"],
        "client_id": data["client_id"],
        "client_name": data["client_name"],
        "token_type": data["token_type"],
        "grant_type": data["grant_type"],
        "ip_address": data["ip_address"],
        "auth_provider": None,
        "flow_id": None,
        "endpoint": data["endpoint"],
        "user_agent": data["user_agent"],
        **{key: data[key] for key in INGRESS_FIELDS},
    }
    if ban is None:
        store.async_record_rejected(entry)
        return
    # The attempt that got the address banned gets an entry of its own, so the
    # moment stays visible instead of disappearing into a folded count.
    entry.update(_ban_marker(ban))
    entry["count"] = 1
    store.async_record(entry)


async def _async_report_login_flow_ban(
    hass: HomeAssistant, request: Any, ban: BanState
) -> None:
    """A wrong password or 2FA code got the IP address banned. Must never raise.

    The attempt itself has already been reported as `login_detail_failed`,
    with the username where it is known. This adds what was missing: one
    `login_detail_request_rejected` with reason ip_banned_now, and the mark on
    the flow's own entry. Only if that entry cannot be found - the flow ID is
    not in the URL, or the entry is gone - does the ban get an entry of its
    own.
    """
    flow_id = _flow_id_of(request)
    data = {
        "ip_address": str(request.remote) if request.remote is not None else None,
        "endpoint": ENDPOINT_LOGIN_FLOW,
        "rejection": REJECTED_INVALID_AUTH_REQUEST,
        **dict.fromkeys(ATTRIBUTION_FIELDS),
        "grant_type": None,
        "flow_id": flow_id,
        "user_agent": _user_agent_of(request),
        **_ingress_of(hass, request),
        **_ban_fields(ban),
    }
    _log_line(RESULT_REQUEST_REJECTED, data)
    hass.bus.async_fire(EVENT_REQUEST_REJECTED, data)

    store = hass.data.get(DOMAIN)
    if not isinstance(store, LoginDetailData):
        return
    if store.async_mark_banned(flow_id, data["ip_address"], _ban_marker(ban)):
        return
    store.async_record(
        {
            "timestamp": dt_util.utcnow().isoformat(),
            "result": RESULT_REQUEST_REJECTED,
            "reason": REJECTED_INVALID_AUTH_REQUEST,
            **dict.fromkeys(ATTRIBUTION_FIELDS),
            "grant_type": None,
            "ip_address": data["ip_address"],
            "auth_provider": None,
            "flow_id": flow_id,
            "endpoint": ENDPOINT_LOGIN_FLOW,
            "user_agent": data["user_agent"],
            **{key: data[key] for key in INGRESS_FIELDS},
            **_ban_marker(ban),
            "count": 1,
        }
    )


def _ban_fields(ban: BanState) -> dict[str, Any]:
    """What the event gets when its request got the IP address banned."""
    return {
        "reason": RESULT_IP_BANNED_NOW,
        "attempts": ban.attempts,
        "threshold": ban.threshold,
    }


def _ban_marker(ban: BanState) -> dict[str, Any]:
    """What the history entry gets when its request got the address banned."""
    return {
        RESULT_IP_BANNED_NOW: True,
        "attempts": ban.attempts,
        "threshold": ban.threshold,
    }


def _banned_now(before: BanState | None, after: BanState | None) -> BanState | None:
    """The state after the call, if this call is what banned the address.

    The ban middleware answers 403 to an address that is already banned, so
    `before` is normally not banned. A websocket can still fail auth after
    its address was banned on another connection; that is not a new ban.
    """
    if after is None or not after.banned:
        return None
    if before is not None and before.banned:
        return None
    return after


def _safe_ban_state(request: Any) -> BanState | None:
    """Ban state of the request's address. Must never raise."""
    try:
        return ban_state(request.app, request.remote)
    except Exception:  # noqa: BLE001 - the ban fields are optional detail
        return None


def _flow_id_of(request: Any) -> str | None:
    """The flow ID in /auth/login_flow/{flow_id}, or None."""
    try:
        flow_id = request.match_info.get("flow_id")
    except Exception:  # noqa: BLE001 - not an aiohttp request
        return None
    return flow_id if isinstance(flow_id, str) and flow_id else None


def _endpoint_of(request: Any) -> str | None:
    """The route pattern of the request, not the literal path.

    `/api/hassio_ingress/{token}/{path}` rather than the path with the token
    in it: path parameters can be secrets, and the query string is never
    read. Falls back to the plain path for a request that matched no route.
    """
    endpoint: str | None = None
    try:
        resource = request.match_info.route.resource
        if resource is not None:
            endpoint = resource.canonical
    except Exception:  # noqa: BLE001 - fall back to the plain path
        endpoint = None
    if not endpoint or not isinstance(endpoint, str):
        endpoint = getattr(request, "path", None)
    return endpoint[:ENDPOINT_MAX_LENGTH] if isinstance(endpoint, str) else None


def _user_agent_of(request: Any) -> str | None:
    """The client's User-Agent header, cut to a fixed length.

    The login flow steps do not get the request, but core keeps the request
    being handled in the `current_request` context variable - the same one
    `get_url` relies on - so it is there for the flow steps too.
    """
    if request is None:
        return None
    try:
        user_agent = request.headers.get("User-Agent")
    except Exception:  # noqa: BLE001 - not an aiohttp request
        return None
    return user_agent[:USER_AGENT_MAX_LENGTH] if user_agent else None


def _ingress_of(hass: HomeAssistant, request: Any) -> dict[str, str | None]:
    """How the request reached Home Assistant - all None without a request.

    `ingress` comes from Home Assistant Cloud itself (core's
    is_cloud_connection), so it can be trusted. Host and X-Forwarded-For are
    headers as the client sent them, before core's forwarded middleware has
    looked at them - X-Forwarded-For is typed by whoever sent the request,
    which is why the field carries "untrusted" in its name.
    """
    found: dict[str, str | None] = dict.fromkeys(INGRESS_FIELDS)
    if request is None:
        return found
    try:
        found["ingress"] = (
            INGRESS_NABU_CASA if is_cloud_connection(hass) else INGRESS_DIRECT
        )
    except Exception:  # noqa: BLE001 - cloud internals; leave it unknown
        found["ingress"] = None
    try:
        headers = request.headers
        found["host"] = clean(headers.get("Host"))
        found["x_forwarded_for_untrusted"] = clean(
            ", ".join(headers.getall("X-Forwarded-For", []))
        )
    except Exception:  # noqa: BLE001 - not an aiohttp request
        pass
    return found


def _provider_of(flow: Any) -> str | None:
    provider = getattr(flow, "_auth_provider", None)
    return getattr(provider, "type", None)


def _ip_of(flow: Any) -> Any:
    context = getattr(flow, "context", None) or {}
    return context.get("ip_address")


def _username_of(flow: Any) -> str | None:
    """Username for a flow that has already got past the password step."""
    credential = getattr(flow, "credential", None)
    if credential is not None:
        username = (credential.data or {}).get("username")
        if username:
            return str(username)
    user = getattr(flow, "user", None)
    return getattr(user, "name", None)


def _user_id_of(flow: Any) -> str | None:
    """User id of a flow that has got past the password step into 2FA."""
    return getattr(getattr(flow, "user", None), "id", None)


async def _async_known_user(
    hass: HomeAssistant, flow: Any, username: str | None
) -> tuple[str | None, str | None]:
    """The stored username and user id, if `username` belongs to a user.

    A failed attempt's username is free text, and a password typed into the
    username field must not end up in the log. So the username is only
    returned if the Home Assistant auth provider knows it - in the form it is
    stored in, not as typed - and (None, None) otherwise.
    """
    provider = getattr(flow, "_auth_provider", None)
    data = getattr(provider, "data", None)
    if data is None:
        return None, None
    wanted = data.normalize_username(username or "")
    # Both lists are walked in full whether the name is known or not, like
    # core's own validate_login, so the time the failed login takes to answer
    # does not tell whether the username exists.
    known: str | None = None
    for stored in data.users:
        if data.normalize_username(stored.get("username") or "") == wanted:
            known = stored["username"]
    user_id: str | None = None
    for user in await hass.auth.async_get_users():
        for credential in user.credentials:
            if (
                credential.auth_provider_type == provider.type
                and credential.auth_provider_id == provider.id
                and data.normalize_username(credential.data.get("username") or "")
                == wanted
            ):
                user_id = user.id
    if not username or known is None:
        return None, None
    # A user that exists in the provider but has never logged in has no
    # credentials and therefore no user to link to.
    return known, user_id


def _unknown_username(hass: HomeAssistant, username: Any) -> str | None:
    """The typed username, if the owner has chosen to keep unknown ones.

    None while the `store_unknown_usernames` option is off. Otherwise the
    username as typed - not normalised, since it belongs to no one - with
    control characters replaced, so it cannot break a log line, and cut to
    UNKNOWN_USERNAME_MAX_LENGTH.
    """
    store = hass.data.get(DOMAIN)
    if not isinstance(store, LoginDetailData) or not store.store_unknown_usernames:
        return None
    if not isinstance(username, str) or not username:
        return None
    cleaned = "".join(
        char if char.isprintable() else "\ufffd"
        for char in username[:UNKNOWN_USERNAME_MAX_LENGTH]
    )
    return cleaned or None


async def _async_user_id_of_result(
    hass: HomeAssistant, flow: Any, flow_result: Any
) -> str | None:
    """User id for a completed login, if the credentials belong to a user.

    Credentials that are new are linked to a user only after this point, by
    core's login flow view, so they have none yet.
    """
    user_id = _user_id_of(flow)
    if user_id is not None:
        return user_id
    if getattr(flow_result, "is_new", True):
        return None
    user = await hass.auth.async_get_user_by_credentials(flow_result)
    return getattr(user, "id", None)


def _username_of_result(flow: Any, flow_result: Any) -> str | None:
    """Username from what core passes on when a step completes.

    HassLoginFlow passes the user_input dict (core has already removed the
    password), while the final result carries a Credentials object.
    """
    if isinstance(flow_result, dict):
        username = flow_result.get("username")
        if username:
            return str(username)
    data = getattr(flow_result, "data", None)
    if isinstance(data, dict) and data.get("username"):
        return str(data["username"])
    return _username_of(flow)


class _CorePatch:
    """The monkeypatch of core's login flow - applied and removable.

    The patch sits on classes in `homeassistant.auth`, not on anything that
    belongs to the config entry. It is therefore managed from here, so that
    removing or reloading the integration in the UI actually takes it off
    again. Reverting only touches methods that are still ours: if someone
    else has patched on top, we leave them alone.
    """

    def __init__(self) -> None:
        # The owner is a class or, for process_wrong_login, a module.
        self._applied: dict[tuple[Any, str], tuple[Any, bool]] = {}
        self._ours: dict[tuple[Any, str], Any] = {}

    @property
    def applied(self) -> bool:
        return bool(self._applied)

    def _install(self, owner: Any, name: str, replacement: Any) -> None:
        original = getattr(owner, name)
        # If the method was inherited, it must be removed again - not copied
        # down onto the subclass as its own.
        was_own = name in owner.__dict__
        self._applied[(owner, name)] = (original, was_own)
        self._ours[(owner, name)] = replacement
        setattr(owner, name, replacement)

    def apply(self, hass: HomeAssistant) -> None:
        """Apply the monkeypatch. Idempotent."""
        if self._applied:
            return

        original_init = HassLoginFlow.async_step_init
        original_mfa = LoginFlow.async_step_mfa
        original_finish_flow = AuthManagerFlowManager.async_finish_flow
        original_wrong_login = http_ban.process_wrong_login
        original_ws_wrong_login = websocket_auth.process_wrong_login
        original_login_flow_wrong_login = getattr(
            auth_login_flow, "process_wrong_login", None
        )
        original_ws_auth = websocket_auth.AuthPhase.async_handle

        async def async_step_init(
            self: HassLoginFlow, user_input: dict[str, str] | None = None
        ) -> Any:
            result = await original_init(self, user_input)
            try:
                if user_input is not None:
                    errors = result.get("errors") or {}
                    if errors.get("base") == "invalid_auth":
                        typed = user_input.get("username")
                        username, user_id = await _async_known_user(hass, self, typed)
                        known_user = True
                        if username is None:
                            unknown = _unknown_username(hass, typed)
                            if unknown is not None:
                                username, known_user = unknown, False
                        _report(
                            hass,
                            "invalid_auth",
                            username,
                            user_id,
                            _ip_of(self),
                            self,
                            self.flow_id,
                            known_user=known_user,
                        )
            except Exception:  # must never break a login
                _LOGGER.exception("login_detail: could not report failed login attempt")
            return result

        async def async_step_mfa(
            self: LoginFlow, user_input: dict[str, str] | None = None
        ) -> Any:
            result = await original_mfa(self, user_input)
            try:
                if user_input is not None:
                    errors = result.get("errors") or {}
                    reason: str | None = None
                    if errors.get("base") == "invalid_code":
                        reason = "invalid_mfa_code"
                    elif result.get("type") == "abort" and result.get("reason") in (
                        "too_many_retry",
                        "login_expired",
                    ):
                        # The last wrong code aborts the flow without an
                        # errors field.
                        reason = f"mfa_{result['reason']}"
                    if reason:
                        _report(
                            hass,
                            reason,
                            _username_of(self),
                            _user_id_of(self),
                            _ip_of(self),
                            self,
                            self.flow_id,
                        )
            except Exception:  # must never break a login
                _LOGGER.exception("login_detail: could not report failed MFA attempt")
            return result

        async def async_finish_flow(
            self: AuthManagerFlowManager, flow: Any, result: Any
        ) -> Any:
            outcome = await original_finish_flow(self, flow, result)
            try:
                # async_finish_flow is only called on CREATE_ENTRY and ABORT.
                # A CREATE_ENTRY in and a form out means exactly one thing:
                # the password was correct, and core now requires 2FA.
                if result.get("type") == FlowResultType.CREATE_ENTRY:
                    outcome_type = outcome.get("type")
                    if (
                        outcome_type == FlowResultType.CREATE_ENTRY
                        and outcome.get("result") is not None
                    ):
                        _report(
                            hass,
                            RESULT_OK,
                            _username_of_result(flow, outcome.get("result")),
                            await _async_user_id_of_result(
                                hass, flow, outcome.get("result")
                            ),
                            _ip_of(flow),
                            flow,
                            flow.flow_id,
                        )
                    elif outcome_type == FlowResultType.FORM:
                        _report(
                            hass,
                            RESULT_MFA_PENDING,
                            _username_of_result(flow, result.get("data")),
                            _user_id_of(flow),
                            _ip_of(flow),
                            flow,
                            flow.flow_id,
                        )
            except Exception:  # must never break a login
                _LOGGER.exception("login_detail: could not report completed login")
            return outcome

        # Each wrapper reads the ban list before and after core's call: core
        # counts the attempt and adds the ban inside it. Only reads - core's
        # ban state is never written to.
        async def process_wrong_login(request: Any) -> None:
            before = _safe_ban_state(request)
            await original_wrong_login(request)
            try:
                await _async_report_rejected(
                    hass, request, ban=_banned_now(before, _safe_ban_state(request))
                )
            except Exception:  # must never break request handling
                _LOGGER.exception("login_detail: could not report rejected request")

        async def ws_process_wrong_login(request: Any) -> None:
            before = _safe_ban_state(request)
            await original_ws_wrong_login(request)
            try:
                await _async_report_rejected(
                    hass,
                    request,
                    websocket=True,
                    ban=_banned_now(before, _safe_ban_state(request)),
                )
            except Exception:  # must never break request handling
                _LOGGER.exception("login_detail: could not report rejected request")

        async def login_flow_process_wrong_login(request: Any) -> None:
            # Only the ban: the wrong password or code is already reported by
            # async_step_init / async_step_mfa above, with the username.
            before = _safe_ban_state(request)
            await original_login_flow_wrong_login(request)
            try:
                ban = _banned_now(before, _safe_ban_state(request))
                if ban is not None:
                    await _async_report_login_flow_ban(hass, request, ban)
            except Exception:  # must never break a login
                _LOGGER.exception("login_detail: could not report an IP ban")

        async def ws_async_handle(self: websocket_auth.AuthPhase, msg: Any) -> Any:
            # Only hands the token on to ws_process_wrong_login, in this task
            # and for this call only. "" for credentials that are not a token.
            token: str | None = None
            if isinstance(msg, dict):
                access_token = msg.get("access_token")
                if isinstance(access_token, str):
                    token = access_token
                elif "access_token" in msg or "api_password" in msg:
                    token = ""
            reset = websocket_token.set(token)
            try:
                return await original_ws_auth(self, msg)
            finally:
                websocket_token.reset(reset)

        self._install(HassLoginFlow, "async_step_init", async_step_init)
        self._install(LoginFlow, "async_step_mfa", async_step_mfa)
        self._install(AuthManagerFlowManager, "async_finish_flow", async_finish_flow)
        # ban.py looks the name up in its own module at call time - that
        # covers the ban middleware (401 on /api/mcp and every other view) and
        # @log_invalid_auth (/auth/token). The websocket imported the name, so
        # it gets its own patch. login_flow.py imported it too; its wrapper
        # only reports a ban (0.11.0), since a wrong password there is already
        # reported above, with the username.
        self._install(http_ban, "process_wrong_login", process_wrong_login)
        self._install(websocket_auth, "process_wrong_login", ws_process_wrong_login)
        if original_login_flow_wrong_login is not None:
            self._install(
                auth_login_flow, "process_wrong_login", login_flow_process_wrong_login
            )
        self._install(websocket_auth.AuthPhase, "async_handle", ws_async_handle)

        _LOGGER.info(
            "login_detail active - logins fire the events %s and %s, "
            "rejected requests fire %s",
            EVENT_LOGIN_FAILED,
            EVENT_LOGIN_SUCCEEDED,
            EVENT_REQUEST_REJECTED,
        )

    def revert(self) -> None:
        """Remove the monkeypatch again."""
        for key, (original, was_own) in self._applied.items():
            owner, name = key
            if owner.__dict__.get(name) is not self._ours[key]:
                # Someone else has patched on top of us. Writing the original
                # back would throw their work away.
                _LOGGER.warning(
                    "login_detail: %s.%s was changed by someone else - leaving it",
                    owner.__name__,
                    name,
                )
                continue
            if was_own:
                setattr(owner, name, original)
            else:
                delattr(owner, name)
        self._applied.clear()
        self._ours.clear()
        _LOGGER.info("login_detail deactivated - the login flow is untouched again")


_PATCH = _CorePatch()


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Import a legacy `login_detail:` line into a config entry, if present.

    The actual setup happens in async_setup_entry. If the line is not in
    configuration.yaml, this function only registers the websocket command -
    the integration is set up from the UI.
    """
    async_register_websocket_commands(hass)
    if DOMAIN in config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data={}
            )
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    data = LoginDetailData(
        hass,
        max_entries=entry.options.get(CONF_MAX_ENTRIES, DEFAULT_MAX_ENTRIES),
        max_days=entry.options.get(CONF_MAX_DAYS, DEFAULT_MAX_DAYS),
        store_unknown_usernames=entry.options.get(
            CONF_STORE_UNKNOWN_USERNAMES, DEFAULT_STORE_UNKNOWN_USERNAMES
        ),
    )
    await data.async_load()
    hass.data[DOMAIN] = data

    await _async_self_check(hass)
    _PATCH.apply(hass)

    entry.async_on_unload(
        async_track_time_interval(hass, data.async_prune_tick, PRUNE_INTERVAL)
    )
    if entry.options.get(CONF_SHOW_PANEL, DEFAULT_SHOW_PANEL):
        await _async_register_panel(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_self_check(hass: HomeAssistant) -> None:
    """Check the private core API of the rejection details, and say so.

    One INFO line when all is well - the post-upgrade check looks for it -
    and a WARNING per feature that had to be switched off.
    """
    try:
        found = await async_self_check(hass)
    except Exception:  # must never block setup
        _LOGGER.exception("login_detail: the self-check failed")
        return
    fallback = {
        "token_identification": (
            "rejected tokens are reported without a reason or user, as before 0.7.1"
        ),
        "token_endpoint": (
            "rejections on /auth/token are reported as invalid_auth_request, "
            "as before 0.10.0"
        ),
    }
    for feature, ok in found.items():
        if not ok:
            _LOGGER.warning(
                "login_detail self-check: %s does not match this Home Assistant "
                "version and is switched off - %s. Please open an issue with "
                "your Home Assistant version",
                feature,
                fallback.get(feature, "it is not reported"),
            )
    if all(found.values()):
        _LOGGER.info(
            "login_detail self-check passed: %s",
            ", ".join(sorted(found)),
        )


async def _async_register_panel(hass: HomeAssistant) -> None:
    """The admin-only "Sign-in log" panel in the sidebar.

    A static path cannot be unregistered, so it is registered once per run
    of Home Assistant; the panel itself comes and goes with the entry. The
    version in the URL makes the browser fetch the module again after an
    upgrade.
    """
    if not hass.data.get(f"{DOMAIN}_static"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_STATIC_URL, str(FRONTEND_DIR), False)]
        )
        hass.data[f"{DOMAIN}_static"] = True
    integration = await async_get_integration(hass, DOMAIN)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=f"{PANEL_STATIC_URL}/{PANEL_MODULE}?v={integration.version}",
        require_admin=True,
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration - including the monkeypatch of core."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        _PATCH.revert()
        frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
        data = hass.data.pop(DOMAIN, None)
        if isinstance(data, LoginDetailData):
            await data.async_flush()
    return unloaded
