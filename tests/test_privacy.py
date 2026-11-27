"""What a user who is not an administrator can see (0.12.0).

Every user of Home Assistant can read every entity and its history, so the
sensors are the one place where Login Detail's data reaches users the
admin-only panel keeps out. These tests read the sensors the way the state
machine does - state and attributes, as JSON - and check that no username, IP
address, hostname, session user or client name is in them unless the owner has
turned on `sensor_details`; that a username no user has is never in a sensor,
an event or the log line; that the storage file is private and deleted with
the integration; and that the IP address sensor stays far below the recorder's
16 KB limit however many addresses there are.

All names and addresses are made up: documentation ranges (RFC 5737) and
names no real person has. They need Home Assistant installed, but not
pytest-homeassistant-custom-component. Without Home Assistant the module is
skipped.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

pytest.importorskip("homeassistant")

from homeassistant.util import dt as dt_util

import custom_components.login_detail as login_detail
from custom_components.login_detail import LoginDetailData, _report
from custom_components.login_detail.const import (
    DOMAIN,
    EVENT_LOGIN_FAILED,
    IP_ATTRIBUTE_LIMIT,
)
from custom_components.login_detail.sensor import (
    LoginCountSensor,
    LoginHistorySensor,
    LoginLatestSensor,
    LoginSessionsSensor,
    LoginUniqueIpSensor,
)

KNOWN_USER = "synthetic-known-user"
UNKNOWN_NAME = "synthetic-typed-in-the-wrong-field"
SESSION_USER = "Synthetic Session Owner"
CLIENT_NAME = "synthetic-token-name"
IP = "192.0.2.10"
OTHER_IP = "198.51.100.20"
SESSION_IP = "203.0.113.30"
HOSTNAME = "synthetic-host.example"

PERSONAL = (KNOWN_USER, UNKNOWN_NAME, SESSION_USER, CLIENT_NAME, IP, OTHER_IP)
PERSONAL += (SESSION_IP, HOSTNAME)


class _Store:
    """Stands in for core's Store and remembers how it was made."""

    instances: ClassVar[list[_Store]] = []

    def __init__(self, hass: Any, version: int, key: str, **kwargs: Any) -> None:
        self.key = key
        self.kwargs = kwargs
        self.loaded: Any = None
        self.saved: Any = None
        self.removed = False
        _Store.instances.append(self)

    async def async_load(self) -> Any:
        return self.loaded

    def async_delay_save(self, data_func: Any, delay: float = 0) -> None:
        self.saved = data_func()

    async def async_save(self, data: Any) -> None:
        self.saved = data

    async def async_remove(self) -> None:
        self.removed = True


class _Bus:
    def __init__(self) -> None:
        self.fired: list[tuple[str, dict[str, Any]]] = []

    def async_fire(self, event_type: str, data: dict[str, Any]) -> None:
        self.fired.append((event_type, dict(data)))


class _Token:
    def __init__(self, token_type: str) -> None:
        self.token_type = token_type
        self.last_used_ip = SESSION_IP
        self.last_used_at = dt_util.utcnow()
        self.client_name = CLIENT_NAME
        self.client_id = None


class _Auth:
    async def async_get_users(self) -> list[Any]:
        user = SimpleNamespace(
            name=SESSION_USER,
            is_active=True,
            refresh_tokens={
                "a": _Token("normal"),
                "b": _Token("long_lived_access_token"),
                "c": _Token("system"),
            },
        )
        return [user]


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.bus = _Bus()
        self.auth = _Auth()

    def async_create_background_task(self, coro: Any, name: str) -> None:
        coro.close()


@pytest.fixture(autouse=True)
def _stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    _Store.instances.clear()
    monkeypatch.setattr(login_detail, "Store", _Store)
    monkeypatch.setattr(login_detail, "async_dispatcher_send", lambda *args: None)


def _entry(**fields: Any) -> dict[str, Any]:
    entry = {
        "timestamp": dt_util.utcnow().isoformat(),
        "result": "invalid_auth",
        "username": KNOWN_USER,
        "user_id": "synthetic-user-id",
        "ip_address": IP,
        "hostname": HOSTNAME,
        "auth_provider": "homeassistant",
        "endpoint": "/auth/login_flow",
        "flow_id": "synthetic-flow",
    }
    entry.update(fields)
    return entry


def _history() -> list[dict[str, Any]]:
    return [
        _entry(),
        _entry(username=UNKNOWN_NAME, user_id=None, known_user=False),
        _entry(result="ok", ip_address=OTHER_IP),
    ]


def _data(*, details: bool, history: list[dict[str, Any]] | None = None) -> Any:
    data = LoginDetailData(_Hass(), sensor_details=details)  # type: ignore[arg-type]
    data.history = _history() if history is None else history
    return data


def _visible(data: Any) -> str:
    """Every sensor's state and attributes, as the state machine has them."""
    sensors: list[Any] = [
        LoginLatestSensor(data),
        LoginCountSensor(data, ok=True),
        LoginCountSensor(data, ok=False),
        LoginUniqueIpSensor(data),
        LoginHistorySensor(data),
    ]
    shown = [
        {"state": sensor.native_value, "attributes": sensor.extra_state_attributes}
        for sensor in sensors
    ]
    sessions = LoginSessionsSensor(data)
    sessions.hass = data.hass
    sessions.async_write_ha_state = lambda: None  # type: ignore[method-assign]
    asyncio.run(sessions._async_refresh())
    shown.append(
        {"state": sessions.native_value, "attributes": sessions.extra_state_attributes}
    )
    return json.dumps(shown)


def test_sensors_show_no_personal_data_by_default() -> None:
    shown = _visible(_data(details=False))
    for value in PERSONAL:
        assert value not in shown, value


def test_sensors_still_count_without_details() -> None:
    data = _data(details=False)
    assert LoginCountSensor(data, ok=False).native_value == 2
    assert LoginCountSensor(data, ok=True).native_value == 1
    assert LoginUniqueIpSensor(data).native_value == 2
    assert LoginLatestSensor(data).native_value == "invalid_auth · /auth/login_flow"
    shown = json.loads(_visible(data))
    assert shown[5]["state"] == 2  # the system token is not a session
    assert {row["type"] for row in shown[5]["attributes"]["sessions"]} == {
        "normal",
        "long_lived_access_token",
    }


def test_details_show_known_users_and_addresses() -> None:
    shown = _visible(_data(details=True))
    for value in (KNOWN_USER, SESSION_USER, CLIENT_NAME, IP, OTHER_IP, SESSION_IP):
        assert value in shown, value
    assert HOSTNAME in shown


def test_unknown_username_is_never_in_a_sensor() -> None:
    assert UNKNOWN_NAME not in _visible(_data(details=True))
    only_unknown = [_entry(username=UNKNOWN_NAME, user_id=None, known_user=False)]
    data = _data(details=True, history=only_unknown)
    assert UNKNOWN_NAME not in _visible(data)
    expected = f"invalid_auth · /auth/login_flow · {IP}"
    assert LoginLatestSensor(data).native_value == expected


def test_unknown_username_only_reaches_the_history(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass = _Hass()
    data = LoginDetailData(hass, store_unknown_usernames=True)  # type: ignore[arg-type]
    hass.data[DOMAIN] = data
    flow = SimpleNamespace(handler=("homeassistant", None))
    with caplog.at_level(logging.INFO):
        _report(
            hass,  # type: ignore[arg-type]
            "invalid_auth",
            UNKNOWN_NAME,
            None,
            IP,
            flow,
            "synthetic-flow",
            known_user=False,
        )
    [(event_type, event)] = hass.bus.fired
    assert event_type == EVENT_LOGIN_FAILED
    assert event["username"] is None
    assert event["known_user"] is False
    assert UNKNOWN_NAME not in caplog.text
    assert "known_user=False" in caplog.text
    assert data.history[0]["username"] == UNKNOWN_NAME
    assert data.history[0]["known_user"] is False


def test_turning_the_option_off_removes_stored_unknown_names() -> None:
    data = LoginDetailData(_Hass(), store_unknown_usernames=False)  # type: ignore[arg-type]
    store = _Store.instances[-1]
    store.loaded = {"history": _history()}
    asyncio.run(data.async_load())
    assert UNKNOWN_NAME not in json.dumps(data.history)
    assert UNKNOWN_NAME not in json.dumps(store.saved)
    assert all("known_user" not in entry for entry in data.history)
    usernames = [entry["username"] for entry in data.history]
    assert usernames == [KNOWN_USER, None, KNOWN_USER]


def test_option_on_keeps_stored_unknown_names() -> None:
    data = LoginDetailData(_Hass(), store_unknown_usernames=True)  # type: ignore[arg-type]
    _Store.instances[-1].loaded = {"history": _history()}
    asyncio.run(data.async_load())
    assert data.history[1]["username"] == UNKNOWN_NAME


def test_storage_file_is_private_and_removed_with_the_entry() -> None:
    LoginDetailData(_Hass())  # type: ignore[arg-type]
    made = _Store.instances[-1]
    assert made.key == f"{DOMAIN}_history"
    assert made.kwargs == {"private": True, "atomic_writes": True}
    asyncio.run(login_detail.async_remove_entry(_Hass(), None))  # type: ignore[arg-type]
    removed = _Store.instances[-1]
    assert removed.removed
    assert removed.kwargs == made.kwargs


def test_existing_file_is_rewritten_once_to_become_private() -> None:
    data = LoginDetailData(_Hass())  # type: ignore[arg-type]
    store = _Store.instances[-1]
    store.loaded = {"history": [_entry()]}
    asyncio.run(data.async_load())
    assert store.saved == {"history": data.history}


def test_core_store_takes_the_arguments(tmp_path: Path) -> None:
    """The real Store must accept what _history_store passes it."""
    from homeassistant.helpers.storage import Store

    hass = SimpleNamespace(
        data={},
        config=SimpleNamespace(
            config_dir=str(tmp_path),
            path=lambda *parts: str(tmp_path.joinpath(*parts)),
        ),
    )
    store = Store(hass, 1, "x", private=True, atomic_writes=True)  # type: ignore[arg-type]
    # 2026.11 moved the file handling, and the flags with it, to `_io`.
    io = getattr(store, "_io", store)
    assert io._private is True
    assert io._atomic_writes is True


def _brute_force(addresses: int) -> list[dict[str, Any]]:
    now = dt_util.utcnow()
    return [
        _entry(
            timestamp=(now - timedelta(seconds=index)).isoformat(),
            ip_address=f"198.18.{index // 256}.{index % 256}",
            hostname="h" * 253,
            username=KNOWN_USER,
            count=index + 1,
        )
        for index in range(addresses)
    ]


def test_ip_sensor_attributes_stay_bounded() -> None:
    data = _data(details=True, history=_brute_force(500))
    sensor = LoginUniqueIpSensor(data)
    attributes = sensor.extra_state_attributes
    assert sensor.native_value == 500
    assert len(attributes["addresses"]) == IP_ATTRIBUTE_LIMIT
    assert attributes["addresses_not_shown"] == 500 - IP_ATTRIBUTE_LIMIT
    # The addresses with the most attempts are the ones shown.
    assert "198.18.1.243" in attributes["addresses"]  # index 499, count 500
    assert len(json.dumps(attributes)) < 16 * 1024


def test_ip_sensor_computes_once_per_write(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _data(details=True)
    sensor = LoginUniqueIpSensor(data)
    calls = 0
    original = sensor._compute_per_ip

    def _counted() -> dict[str, dict[str, Any]]:
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(sensor, "_compute_per_ip", _counted)

    def _write() -> None:
        # What Home Assistant reads when it writes the state.
        sensor.native_value  # noqa: B018
        sensor.extra_state_attributes  # noqa: B018

    monkeypatch.setattr(sensor, "async_write_ha_state", _write)
    sensor._async_write_if_changed()
    assert calls == 1
    data.history.insert(0, _entry(ip_address="192.0.2.99"))
    sensor._async_write_if_changed()
    assert calls == 2
    assert sensor.native_value == 3
