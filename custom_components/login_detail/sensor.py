"""Sensors for login_detail - who logs in, when, and from which IP address.

The entities are deliberately few and flat:

  sensor.login_seneste_haendelse   latest login (ok or failed)
  sensor.login_ok_24t              completed logins in the last 24 hours
  sensor.login_mislykkede_24t      failed login attempts and rejected requests
                                   in the last 24 hours, per endpoint
  sensor.login_ip_adresser_24t     unique IP addresses in the last 24 hours
  sensor.login_historik            the most recent stored events (attribute)
  sensor.login_aktive_sessioner    active tokens with the last used IP address

No entity exposes passwords, tokens or keys. The sessions sensor only reads
metadata (user, IP address, time, client name) through hass.auth - never
.storage/auth directly.

The entity names, attribute keys and units below are Danish. They are kept
unchanged on purpose: existing installations have entity IDs, dashboards and
templates that depend on them. See "Known limitations" in README.md.
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

from .const import (
    DOMAIN,
    HISTORY_ATTRIBUTE_LIMIT,
    RESULT_OK,
    SIGNAL_UPDATED,
    UPDATE_INTERVAL,
    WINDOW,
)

TOKEN_TYPE_SYSTEM = "system"


def _tally(values: Any) -> dict[str, int]:
    """Count with str keys - orjson cannot serialise None as a key."""
    counted = Counter(str(value) if value is not None else "ukendt" for value in values)
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
        counted[str(value) if value is not None else "ukendt"] += _weight(entry)
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
            LoginSessionsSensor(),
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


def _as_text(entry: dict[str, Any]) -> dict[str, Any]:
    """History entry in the shape the attributes show it."""
    return {
        "tidspunkt": _local(entry.get("timestamp")),
        "resultat": entry.get("result"),
        "bruger": entry.get("username"),
        "ip": entry.get("ip_address"),
        "vaert": entry.get("hostname"),
        "metode": entry.get("auth_provider"),
        "endpoint": entry.get("endpoint"),
        "count": _weight(entry),
    }


class LoginDetailSensor(SensorEntity):
    """Shared update pattern: push on a new event plus a slow timer.

    The timer is needed because the 24-hour counters must drop on their own
    as the window slides - not only when someone logs in.
    """

    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(self, data: Any) -> None:
        self._data = data
        self._last_written: tuple[Any, Any] | None = None

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

    _attr_name = "Login seneste hændelse"
    _attr_unique_id = "login_detail_latest"
    _attr_icon = "mdi:login-variant"

    @property
    def native_value(self) -> str | None:
        if not self._data.history:
            return None
        entry = self._data.history[0]
        # A rejected request has no username; the endpoint says more.
        who = entry.get("username") or entry.get("endpoint")
        value = f"{entry.get('result')} · {who} · {entry.get('ip_address')}"
        return value[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._data.history:
            return {}
        return _as_text(self._data.history[0])


class LoginCountSensor(LoginDetailSensor):
    """Number of completed or failed logins in the window."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "logins"

    def __init__(self, data: Any, ok: bool) -> None:
        super().__init__(data)
        self._ok = ok
        if ok:
            self._attr_name = "Login ok 24t"
            self._attr_unique_id = "login_detail_ok_24h"
            self._attr_icon = "mdi:account-check"
        else:
            self._attr_name = "Login mislykkede 24t"
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
        # Rejected requests have no username; leave them out of that tally
        # instead of counting them as "ukendt".
        with_username = [entry for entry in matching if entry.get("username")]
        return {
            "ip_fordeling": _tally_entries(matching, "ip_address"),
            "brugernavne": _tally_entries(with_username, "username"),
            "endpoints": _tally_entries(matching, "endpoint"),
            "seneste": _local(matching[0].get("timestamp")) if matching else None,
        }


class LoginUniqueIpSensor(LoginDetailSensor):
    """Unique IP addresses that tried to log in within the window."""

    _attr_name = "Login IP-adresser 24t"
    _attr_unique_id = "login_detail_unique_ip_24h"
    _attr_icon = "mdi:ip-network"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "adresser"

    def _per_ip(self) -> dict[str, dict[str, Any]]:
        per_ip: dict[str, dict[str, Any]] = {}
        for entry in self._window():
            ip_address = entry.get("ip_address") or "ukendt"
            row = per_ip.setdefault(
                ip_address,
                {
                    "vaert": entry.get("hostname"),
                    "ok": 0,
                    "mislykkede": 0,
                    "seneste": _local(entry.get("timestamp")),
                    "brugere": [],
                    "endpoints": [],
                },
            )
            if entry.get("result") == RESULT_OK:
                row["ok"] += _weight(entry)
            else:
                row["mislykkede"] += _weight(entry)
            username = entry.get("username")
            if username and username not in row["brugere"]:
                row["brugere"].append(username)
            endpoint = entry.get("endpoint")
            if endpoint and endpoint not in row["endpoints"]:
                row["endpoints"].append(endpoint)
        return per_ip

    @property
    def native_value(self) -> int:
        return len(self._per_ip())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"adresser": self._per_ip()}


class LoginHistorySensor(LoginDetailSensor):
    """The most recently stored login events as an attribute."""

    _attr_name = "Login historik"
    _attr_unique_id = "login_detail_history"
    _attr_icon = "mdi:format-list-bulleted"
    _attr_native_unit_of_measurement = "hændelser"

    @property
    def native_value(self) -> int:
        return len(self._data.history)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "hændelser": [
                _as_text(entry)
                for entry in self._data.history[:HISTORY_ATTRIBUTE_LIMIT]
            ]
        }


class LoginSessionsSensor(SensorEntity):
    """Active tokens and the IP address they were last used from.

    This is where you see that someone is *still* logged in - unlike the
    history, which only shows the moment of login. System tokens (Supervisor
    and others) are not counted; they do not belong to a person.
    """

    _attr_should_poll = False
    _attr_has_entity_name = False
    _attr_name = "Login aktive sessioner"
    _attr_unique_id = "login_detail_sessions"
    _attr_icon = "mdi:account-key"
    _attr_native_unit_of_measurement = "sessioner"

    def __init__(self) -> None:
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
                    sessions.append(
                        {
                            "bruger": user.name,
                            "ip": token.last_used_ip,
                            "sidst_brugt": (
                                dt_util.as_local(token.last_used_at)
                                .replace(microsecond=0)
                                .isoformat()
                                if token.last_used_at
                                else None
                            ),
                            "type": token.token_type,
                            "klient": token.client_name or token.client_id,
                        }
                    )
        except Exception:  # noqa: BLE001 - a sensor must not break startup
            return
        sessions.sort(key=lambda row: row["sidst_brugt"] or "", reverse=True)
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
        return {
            "sessioner": self._sessions,
            "ip_fordeling": _tally(row["ip"] for row in self._sessions),
        }
