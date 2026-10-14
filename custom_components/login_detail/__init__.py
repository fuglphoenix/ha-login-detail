"""Login Detail - who tries to log in, when, and from which IP address.

On a failed login attempt Home Assistant logs only the IP address and URL,
never the attempted username, and a *successful* login is not logged at all.
The former is a known, open gap in core: home-assistant/core#148110.

This integration monkeypatches the login flow and fires two events:

  login_detail_failed      username, ip_address, reason, flow_id, auth_provider,
                           endpoint
  login_detail_succeeded   username, ip_address, flow_id, auth_provider, endpoint

It also wraps core's `process_wrong_login`, which handles every request that
is rejected for invalid authentication - a wrong or expired token on
/api/mcp, /api/websocket, /auth/token and so on. Those are not login flows and
carry no username, so they get their own event:

  login_detail_request_rejected   ip_address, endpoint, reason

Failed login attempts are logged at WARNING, successful logins at INFO. The
events are stored in `.storage/login_detail_history` and exposed as sensors
(see sensor.py), so the history survives a restart.

Passwords and TOTP codes are never read, logged or forwarded.

Setup
-----
The integration is set up from the UI: Settings -> Devices & services ->
Add integration -> Login Detail. There is nothing to configure.

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
`AuthManagerFlowManager.async_finish_flow`, and `process_wrong_login` in
`homeassistant.components.http.ban` and
`homeassistant.components.websocket_api.auth`) and may stop working with any
core upgrade. If patching fails, it is logged at startup and login continues
unchanged.
"""

from __future__ import annotations

import logging
import socket
from typing import Any

from homeassistant.auth import AuthManagerFlowManager
from homeassistant.auth.providers import LoginFlow
from homeassistant.auth.providers.homeassistant import HassLoginFlow
from homeassistant.components.http import ban as http_ban
from homeassistant.components.websocket_api import auth as websocket_auth
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    ENDPOINT_LOGIN_FLOW,
    EVENT_LOGIN_FAILED,
    EVENT_LOGIN_SUCCEEDED,
    EVENT_REQUEST_REJECTED,
    HISTORY_LIMIT,
    REJECTED_FOLD_WINDOW,
    RESULT_MFA_PENDING,
    RESULT_OK,
    RESULT_REQUEST_REJECTED,
    SIGNAL_UPDATED,
)

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


class LoginDetailData:
    """History of login events, persisted to disk."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.history: list[dict[str, Any]] = []
        self._host_cache: dict[str, str | None] = {}

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
            self.history = stored["history"][:HISTORY_LIMIT]

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
        del self.history[HISTORY_LIMIT:]
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
        attempt out of the 50-entry history. A repeat from the same IP address
        on the same endpoint within REJECTED_FOLD_WINDOW raises the count of
        the existing entry and moves it to the front instead.
        """
        now = dt_util.parse_datetime(entry["timestamp"])
        for index, previous in enumerate(self.history):
            if (
                previous.get("result") != RESULT_REQUEST_REJECTED
                or previous.get("ip_address") != entry.get("ip_address")
                or previous.get("endpoint") != entry.get("endpoint")
            ):
                continue
            seen = dt_util.parse_datetime(previous.get("timestamp") or "")
            if now is None or seen is None or now - seen > REJECTED_FOLD_WINDOW:
                break
            del self.history[index]
            previous["timestamp"] = entry["timestamp"]
            previous["count"] = int(previous.get("count") or 1) + 1
            self.history.insert(0, previous)
            self._async_save()
            async_dispatcher_send(self.hass, SIGNAL_UPDATED)
            return
        entry["count"] = 1
        self.async_record(entry)

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


def _report(
    hass: HomeAssistant,
    result: str,
    username: str | None,
    ip_address: Any,
    flow: Any,
    flow_id: str,
) -> None:
    """Log and fire one login event. Must never raise."""
    data = {
        "username": username,
        "ip_address": str(ip_address) if ip_address is not None else None,
        "reason": result,
        "flow_id": flow_id,
        "auth_provider": _provider_of(flow),
        "endpoint": ENDPOINT_LOGIN_FLOW,
    }

    if result == RESULT_OK:
        del data["reason"]
        _LOGGER.info(
            "Login ok: user=%s ip=%s method=%s flow=%s",
            data["username"],
            data["ip_address"],
            data["auth_provider"],
            flow_id,
        )
        hass.bus.async_fire(EVENT_LOGIN_SUCCEEDED, data)
    elif result == RESULT_MFA_PENDING:
        _LOGGER.info(
            "Password correct, waiting for 2FA: user=%s ip=%s flow=%s",
            data["username"],
            data["ip_address"],
            flow_id,
        )
        hass.bus.async_fire(EVENT_LOGIN_FAILED, data)
    else:
        _LOGGER.warning(
            "Failed login: user=%s ip=%s reason=%s flow=%s",
            data["username"],
            data["ip_address"],
            result,
            flow_id,
        )
        hass.bus.async_fire(EVENT_LOGIN_FAILED, data)

    # The monkeypatch sits on core classes and has no config entry to look up.
    # That is why the history lives in hass.data and not in entry.runtime_data.
    store = hass.data.get(DOMAIN)
    if not isinstance(store, LoginDetailData):
        return
    store.async_record(
        {
            "timestamp": dt_util.utcnow().isoformat(),
            "result": result,
            "username": data["username"],
            "ip_address": data["ip_address"],
            "auth_provider": data["auth_provider"],
            "flow_id": flow_id,
            "endpoint": ENDPOINT_LOGIN_FLOW,
        }
    )


def _report_rejected(hass: HomeAssistant, request: Any) -> None:
    """Log and fire one rejected request. Must never raise.

    Core has already logged the full URL at WARNING. This line only adds what
    the history stores, so it is INFO.
    """
    data = {
        "ip_address": str(request.remote) if request.remote is not None else None,
        "endpoint": _endpoint_of(request),
        "reason": RESULT_REQUEST_REJECTED,
    }
    _LOGGER.info(
        "Request rejected: ip=%s endpoint=%s", data["ip_address"], data["endpoint"]
    )
    hass.bus.async_fire(EVENT_REQUEST_REJECTED, data)

    store = hass.data.get(DOMAIN)
    if not isinstance(store, LoginDetailData):
        return
    store.async_record_rejected(
        {
            "timestamp": dt_util.utcnow().isoformat(),
            "result": RESULT_REQUEST_REJECTED,
            "username": None,
            "ip_address": data["ip_address"],
            "auth_provider": None,
            "flow_id": None,
            "endpoint": data["endpoint"],
        }
    )


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
    if not endpoint:
        endpoint = getattr(request, "path", None)
    return endpoint[:ENDPOINT_MAX_LENGTH] if endpoint else None


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

        async def async_step_init(
            self: HassLoginFlow, user_input: dict[str, str] | None = None
        ) -> Any:
            result = await original_init(self, user_input)
            try:
                if user_input is not None:
                    errors = result.get("errors") or {}
                    if errors.get("base") == "invalid_auth":
                        _report(
                            hass,
                            "invalid_auth",
                            user_input.get("username"),
                            _ip_of(self),
                            self,
                            self.flow_id,
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
                            _ip_of(flow),
                            flow,
                            flow.flow_id,
                        )
                    elif outcome_type == FlowResultType.FORM:
                        _report(
                            hass,
                            RESULT_MFA_PENDING,
                            _username_of_result(flow, result.get("data")),
                            _ip_of(flow),
                            flow,
                            flow.flow_id,
                        )
            except Exception:  # must never break a login
                _LOGGER.exception("login_detail: could not report completed login")
            return outcome

        async def process_wrong_login(request: Any) -> None:
            await original_wrong_login(request)
            try:
                _report_rejected(hass, request)
            except Exception:  # must never break request handling
                _LOGGER.exception("login_detail: could not report rejected request")

        self._install(HassLoginFlow, "async_step_init", async_step_init)
        self._install(LoginFlow, "async_step_mfa", async_step_mfa)
        self._install(AuthManagerFlowManager, "async_finish_flow", async_finish_flow)
        # ban.py looks the name up in its own module at call time - that
        # covers the ban middleware (401 on /api/mcp and every other view) and
        # @log_invalid_auth (/auth/token). The websocket imported the name, so
        # it gets its own patch. login_flow.py imported it too and is left
        # alone on purpose: a wrong password there is already reported above,
        # with the username.
        self._install(http_ban, "process_wrong_login", process_wrong_login)
        self._install(websocket_auth, "process_wrong_login", process_wrong_login)

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
    configuration.yaml, this function does nothing - the integration is set
    up from the UI.
    """
    if DOMAIN in config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data={}
            )
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    data = LoginDetailData(hass)
    await data.async_load()
    hass.data[DOMAIN] = data

    _PATCH.apply(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration - including the monkeypatch of core."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        _PATCH.revert()
        data = hass.data.pop(DOMAIN, None)
        if isinstance(data, LoginDetailData):
            await data.async_flush()
    return unloaded
