"""Sensors for login_detail - who logs in, when, and from which IP address.

The entities are deliberately few and flat. Entity IDs on a new installation
(an existing installation keeps the Danish IDs it already has, see below):

  sensor.login_latest_event        latest login (ok or failed)
  sensor.login_ok_24h              completed logins in the last 24 hours
  sensor.login_failed_24h          failed login attempts and rejected requests
                                   in the last 24 hours, per endpoint
  sensor.login_ip_addresses_24h    unique IP addresses in the last 24 hours
  sensor.login_history             the most recent stored events (attribute)
  sensor.login_active_sessions     active tokens with the last used IP address

No entity exposes passwords, tokens or keys. The sessions sensor only reads
metadata (user, IP address, time, client name) through hass.auth - never
.storage/auth directly.

Every user of Home Assistant can read every entity, administrator or not, and
the recorder keeps the attributes. So since 0.12.0 the sensors carry no
usernames, IP addresses, hostnames or session users unless the owner turns on
the `sensor_details` option; the admin-only panel shows them either way. A
username no user has (the `store_unknown_usernames` option) is never shown in
a sensor, not even then - it may be a password typed into the wrong field.

The entity names, units and attribute keys are English. The unique IDs must
never change: the entity registry finds an existing entity by its unique ID and
keeps its entity ID, so installations from before 0.8.0 keep
sensor.login_mislykkede_24t and the others. The attribute keys changed in
0.8.0; README.md has the old and new keys.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .ban_status import banned_addresses, is_banned
from .const import (
    DOMAIN,
    HISTORY_ATTRIBUTE_LIMIT,
    IP_ATTRIBUTE_LIMIT,
    IP_ATTRIBUTE_LIST_LIMIT,
    RESULT_IP_BANNED_NOW,
    RESULT_OK,
    SIGNAL_UPDATED,
    UPDATE_INTERVAL,
    WINDOW,
)

TOKEN_TYPE_SYSTEM = "system"


def _tally(values: Any) -> dict[str, int]:
    """Count with str keys - orjson cannot serialise None as a key."""
    counted = Counter(
        str(value) if value is not None else "unknown" for value in values
    )
    return dict(counted.most_common(10))


def _weight(entry: dict[str, Any]) -> int:
    """Attempts behind one history entry - rejected requests are folded."""
    try:
        return max(int(entry.get("count") or 1), 1)
    except (TypeError, ValueError):
        return 1


def _tally_entries(entries: list[dict[str, Any]], key: str) -> dict[str, int]:
    """Like _tally, but each entry counts as many attempts as it folds."""
    counted: Counter[str] = Counter()
    for entry in entries:
        value = entry.get(key)
        counted[str(value) if value is not None else "unknown"] += _weight(entry)
    return dict(counted.most_common(10))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors from the config entry.

    The entities' unique_id is unchanged since 0.2.0, so entity IDs, names
    and history carry over from the YAML setup.
    """
    data = hass.data[DOMAIN]
    async_add_entities(
        [
            LoginLatestSensor(data),
            LoginCountSensor(data, ok=True),
            LoginCountSensor(data, ok=False),
            LoginUniqueIpSensor(data),
            LoginHistorySensor(data),
            LoginSessionsSensor(data),
        ]
    )


def _local(timestamp: str | None) -> str | None:
    """ISO UTC from the history to local time, rounded to seconds."""
    if not timestamp:
        return None
    parsed = dt_util.parse_datetime(timestamp)
    if parsed is None:
        return None
    return dt_util.as_local(parsed).replace(microsecond=0).isoformat()


def _username(entry: dict[str, Any]) -> str | None:
    """The username a sensor may show - never one no user has."""
    if entry.get("known_user") is False:
        return None
    return entry.get("username")


def _as_text(entry: dict[str, Any], details: bool) -> dict[str, Any]:
    """History entry in the shape the attributes show it."""
    text: dict[str, Any] = {"time": _local(entry.get("timestamp"))}
    text["result"] = entry.get("result")
    if details:
        text["username"] = _username(entry)
        text["ip"] = entry.get("ip_address")
        text["hostname"] = entry.get("hostname")
    text["auth_provider"] = entry.get("auth_provider")
    text["endpoint"] = entry.get("endpoint")
    text["count"] = _weight(entry)
    # Only the entry that got the IP address banned has these (0.11.0); every
    # other entry is left without three nulls.
    if entry.get(RESULT_IP_BANNED_NOW):
        text[RESULT_IP_BANNED_NOW] = True
        text["attempts"] = entry.get("attempts")
        text["threshold"] = entry.get("threshold")
    return text


class LoginDetailSensor(SensorEntity):
    """Shared update pattern: push on a new event plus a slow timer.

    The timer is needed because the 24-hour counters must drop on their own
    as the window slides - not only when someone logs in.
    """

    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(self, data: Any) -> None:
        self._data = data
        self._details: bool = getattr(data, "sensor_details", False)
        self._last_written: tuple[Any, Any] | None = None

    def _invalidate(self) -> None:
        """Forget anything computed for the previous write."""

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._async_updated)
        )
        self.async_on_remove(
            async_track_time_interval(self.hass, self._async_interval, UPDATE_INTERVAL)
        )

    @callback
    def _async_write_if_changed(self) -> None:
        """Write only when something has actually changed.

        The timer ticks every 5 minutes, but logins are rare. Without this,
        every tick would add a recorder row with no new content.
        """
        self._invalidate()
        snapshot = (self.native_value, self.extra_state_attributes)
        if snapshot == self._last_written:
            return
        self._last_written = snapshot
        self.async_write_ha_state()

    @callback
    def _async_updated(self) -> None:
        self._async_write_if_changed()

    @callback
    def _async_interval(self, now: datetime) -> None:
        self._async_write_if_changed()

    def _window(self) -> list[dict[str, Any]]:
        """Events within the last 24 hours, newest first.

        The whole list is walked on purpose. An early `break` would assume
        the history is always sorted - and that does not hold if the storage
        file has been edited or the clock has jumped. The list is at most
        `max_entries` long (5000 at the very most), so there is little to save.
        """
        cutoff = dt_util.utcnow() - WINDOW
        recent = []
        for entry in self._data.history:
            parsed = dt_util.parse_datetime(entry.get("timestamp") or "")
            if parsed is None or parsed < cutoff:
                continue
            recent.append(entry)
        recent.sort(key=lambda entry: entry.get("timestamp") or "", reverse=True)
        return recent


class LoginLatestSensor(LoginDetailSensor):
    """Latest login event, in readable form."""

    _attr_name = "Login latest event"
    _attr_unique_id = "login_detail_latest"
    _attr_icon = "mdi:login-variant"

    @property
    def native_value(self) -> str | None:
        if not self._data.history:
            return None
        entry = self._data.history[0]
        if not self._details:
            return f"{entry.get('result')} · {entry.get('endpoint')}"[:255]
        # A rejected request has no username; the endpoint says more.
        who = _username(entry) or entry.get("endpoint")
        value = f"{entry.get('result')} · {who} · {entry.get('ip_address')}"
        return value[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._data.history:
            return {}
        return _as_text(self._data.history[0], self._details)


class LoginCountSensor(LoginDetailSensor):
    """Number of completed or failed logins in the window."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "logins"

    def __init__(self, data: Any, ok: bool) -> None:
        super().__init__(data)
        self._ok = ok
        if ok:
            self._attr_name = "Login ok 24h"
            self._attr_unique_id = "login_detail_ok_24h"
            self._attr_icon = "mdi:account-check"
        else:
            self._attr_name = "Login failed 24h"
            self._attr_unique_id = "login_detail_failed_24h"
            self._attr_icon = "mdi:account-alert"

    def _matching(self) -> list[dict[str, Any]]:
        return [
            entry
            for entry in self._window()
            if (entry.get("result") == RESULT_OK) is self._ok
        ]

    @property
    def native_value(self) -> int:
        return sum(_weight(entry) for entry in self._matching())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        matching = self._matching()
        attributes: dict[str, Any] = {}
        if self._details:
            # Rejected requests have no username; leave them out of that tally
            # instead of counting them as "unknown". So are the names no user
            # has.
            with_username = [entry for entry in matching if _username(entry)]
            attributes["ip_addresses"] = _tally_entries(matching, "ip_address")
            attributes["usernames"] = _tally_entries(with_username, "username")
        attributes["endpoints"] = _tally_entries(matching, "endpoint")
        attributes["latest"] = (
            _local(matching[0].get("timestamp")) if matching else None
        )
        return attributes


class LoginUniqueIpSensor(LoginDetailSensor):
    """Unique IP addresses that tried to log in within the window."""

    _attr_name = "Login IP addresses 24h"
    _attr_unique_id = "login_detail_unique_ip_24h"
    _attr_icon = "mdi:ip-network"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "addresses"

    def __init__(self, data: Any) -> None:
        super().__init__(data)
        self._rows: dict[str, dict[str, Any]] | None = None

    def _invalidate(self) -> None:
        self._rows = None

    def _per_ip(self) -> dict[str, dict[str, Any]]:
        """All addresses in the window, computed once per write.

        Home Assistant reads the state and the attributes twice per write -
        once for the change check, once to write them.
        """
        if self._rows is None:
            self._rows = self._compute_per_ip()
        return self._rows

    def _compute_per_ip(self) -> dict[str, dict[str, Any]]:
        # Core's ban list, read once per update and never written to (0.11.0).
        # `banned` is the state now, not at the time of the attempts, and is
        # always false when ip_ban_enabled is off.
        banned = banned_addresses(self.hass) if self.hass is not None else set()
        per_ip: dict[str, dict[str, Any]] = {}
        for entry in self._window():
            ip_address = entry.get("ip_address") or "unknown"
            row = per_ip.setdefault(
                ip_address,
                {
                    "hostname": entry.get("hostname"),
                    "ok": 0,
                    "failed": 0,
                    "latest": _local(entry.get("timestamp")),
                    "usernames": [],
                    "endpoints": [],
                    "banned": is_banned(banned, entry.get("ip_address")),
                },
            )
            if entry.get("result") == RESULT_OK:
                row["ok"] += _weight(entry)
            else:
                row["failed"] += _weight(entry)
            username = _username(entry)
            if username and username not in row["usernames"]:
                row["usernames"].append(username)
            endpoint = entry.get("endpoint")
            if endpoint and endpoint not in row["endpoints"]:
                row["endpoints"].append(endpoint)
        return per_ip

    @property
    def native_value(self) -> int:
        return len(self._per_ip())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        per_ip = self._per_ip()
        banned = sum(1 for row in per_ip.values() if row["banned"])
        if not self._details:
            return {"banned": banned}
        # The addresses with the most attempts, so the attributes stay well
        # below the recorder's limit however many addresses there are.
        top = sorted(
            per_ip.items(),
            key=lambda item: item[1]["ok"] + item[1]["failed"],
            reverse=True,
        )[:IP_ATTRIBUTE_LIMIT]
        return {
            "addresses": {
                ip_address: {
                    **row,
                    "usernames": row["usernames"][:IP_ATTRIBUTE_LIST_LIMIT],
                    "endpoints": row["endpoints"][:IP_ATTRIBUTE_LIST_LIMIT],
                }
                for ip_address, row in top
            },
            "addresses_not_shown": len(per_ip) - len(top),
            "banned": banned,
        }


class LoginHistorySensor(LoginDetailSensor):
    """The most recently stored login events as an attribute."""

    _attr_name = "Login history"
    _attr_unique_id = "login_detail_history"
    _attr_icon = "mdi:format-list-bulleted"
    _attr_native_unit_of_measurement = "events"

    @property
    def native_value(self) -> int:
        return len(self._data.history)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "events": [
                _as_text(entry, self._details)
                for entry in self._data.history[:HISTORY_ATTRIBUTE_LIMIT]
            ]
        }


class LoginSessionsSensor(SensorEntity):
    """Active tokens and the IP address they were last used from.

    This is where you see that someone is *still* logged in - unlike the
    history, which only shows the moment of login. System tokens (Supervisor
    and others) are not counted; they do not belong to a person. Without the
    `sensor_details` option a session is only its type and when it was last
    used: no user, no IP address, no client name.
    """

    _attr_should_poll = False
    _attr_has_entity_name = False
    _attr_name = "Login active sessions"
    _attr_unique_id = "login_detail_sessions"
    _attr_icon = "mdi:account-key"
    _attr_native_unit_of_measurement = "sessions"

    def __init__(self, data: Any) -> None:
        self._details: bool = getattr(data, "sensor_details", False)
        self._sessions: list[dict[str, Any]] = []
        self._written: list[dict[str, Any]] | None = None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._async_updated)
        )
        self.async_on_remove(
            async_track_time_interval(self.hass, self._async_interval, UPDATE_INTERVAL)
        )
        await self._async_refresh()

    @callback
    def _async_updated(self) -> None:
        self.hass.async_create_task(self._async_refresh())

    async def _async_interval(self, now: datetime) -> None:
        await self._async_refresh()

    async def _async_refresh(self) -> None:
        sessions: list[dict[str, Any]] = []
        try:
            for user in await self.hass.auth.async_get_users():
                if not user.is_active:
                    continue
                for token in user.refresh_tokens.values():
                    if token.token_type == TOKEN_TYPE_SYSTEM:
                        continue
                    session: dict[str, Any] = {
                        "last_used": (
                            dt_util.as_local(token.last_used_at)
                            .replace(microsecond=0)
                            .isoformat()
                            if token.last_used_at
                            else None
                        ),
                        "type": token.token_type,
                    }
                    if self._details:
                        session["user"] = user.name
                        session["ip"] = token.last_used_ip
                        session["client"] = token.client_name or token.client_id
                    sessions.append(session)
        except Exception:  # noqa: BLE001 - a sensor must not break startup
            return
        sessions.sort(key=lambda row: row["last_used"] or "", reverse=True)
        self._sessions = sessions
        # Same reasoning as in LoginDetailSensor: the timer must not write a
        # recorder row every 5 minutes with no new content.
        if sessions == self._written:
            return
        self._written = [dict(row) for row in sessions]
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return len(self._sessions)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._details:
            return {"sessions": self._sessions}
        return {
            "sessions": self._sessions,
            "ip_addresses": _tally(row["ip"] for row in self._sessions),
        }
