"""IP ban detail (0.11.0): the request that got the address banned, and
`banned` per IP.

These run core's real `process_wrong_login` and `setup_bans` against a bare
aiohttp application, so they need Home Assistant and
pytest-homeassistant-custom-component installed - pinned to the same Home
Assistant version, see tests/README.md. Without them the module is skipped.
"""

from __future__ import annotations

from ipaddress import ip_address
from socket import herror
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from homeassistant.components.auth import login_flow as auth_login_flow
from homeassistant.components.http import ban as http_ban
from homeassistant.components.http.const import KEY_HASS
from homeassistant.components.websocket_api import auth as websocket_auth
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.login_detail import _PATCH, LoginDetailData
from custom_components.login_detail.const import (
    DOMAIN,
    EVENT_REQUEST_REJECTED,
    RESULT_IP_BANNED_NOW,
    RESULT_REQUEST_REJECTED,
)
from custom_components.login_detail.sensor import LoginUniqueIpSensor, _as_text
from custom_components.login_detail.websocket import as_log_entry

ATTACKER = "203.0.113.7"
OTHER = "198.51.100.2"
FLOW_ID = "0123456789abcdef"


class _Http:
    def __init__(self, app: web.Application) -> None:
        self.app = app


@pytest.fixture
def app(hass: HomeAssistant) -> web.Application:
    """An aiohttp app with core's ban setup, threshold 2."""
    app = web.Application()
    app[KEY_HASS] = hass
    http_ban.setup_bans(hass, app, 2)
    return app


@pytest.fixture
def events(hass: HomeAssistant) -> list[dict[str, Any]]:
    received: list[dict[str, Any]] = []
    hass.bus.async_listen(EVENT_REQUEST_REJECTED, lambda e: received.append(e.data))
    return received


@pytest.fixture(autouse=True)
def patched(hass: HomeAssistant):
    """Our patch applied, no DNS, no ip_bans.yaml written."""
    hass.data[DOMAIN] = LoginDetailData(hass)
    _PATCH.apply(hass)
    with (
        patch.object(http_ban, "gethostbyaddr", side_effect=herror),
        patch.object(http_ban.IpBanManager, "_add_ban"),
    ):
        yield
    _PATCH.revert()
    hass.data.pop(DOMAIN, None)


def _request(app: web.Application, remote: str = ATTACKER) -> web.Request:
    return make_mocked_request("GET", "/api/mcp", app=app).clone(remote=remote)


def _login_flow_request(
    app: web.Application, remote: str = ATTACKER, flow_id: str = FLOW_ID
) -> web.Request:
    return make_mocked_request(
        "POST", f"/auth/login_flow/{flow_id}", app=app, match_info={"flow_id": flow_id}
    ).clone(remote=remote)


def _failed_login(remote: str = ATTACKER, flow_id: str = FLOW_ID) -> dict[str, Any]:
    """The entry async_step_init has stored before login_flow.py counts it."""
    return {
        "timestamp": dt_util.utcnow().isoformat(),
        "result": "invalid_auth",
        "username": "probe-admin",
        "ip_address": remote,
        "flow_id": flow_id,
        "endpoint": "/auth/login_flow",
    }


async def test_request_that_bans_is_marked(
    hass: HomeAssistant, app: web.Application, events: list[dict[str, Any]]
) -> None:
    await http_ban.process_wrong_login(_request(app))
    await http_ban.process_wrong_login(_request(app))
    await hass.async_block_till_done()

    assert ip_address(ATTACKER) in app[http_ban.KEY_BAN_MANAGER].ip_bans_lookup
    assert events[0]["reason"] == RESULT_REQUEST_REJECTED
    assert "attempts" not in events[0]
    assert events[1]["reason"] == RESULT_IP_BANNED_NOW
    assert events[1]["rejection"] == "no_credentials"
    assert events[1]["attempts"] == 2
    assert events[1]["threshold"] == 2
    assert events[1]["endpoint"] == "/api/mcp"

    history = hass.data[DOMAIN].history
    # The ban gets its own entry instead of being folded into the first.
    assert len(history) == 2
    assert history[0][RESULT_IP_BANNED_NOW] is True
    assert history[0]["result"] == RESULT_REQUEST_REJECTED
    assert history[0]["reason"] == "no_credentials"
    assert history[0]["count"] == 1
    assert (history[0]["attempts"], history[0]["threshold"]) == (2, 2)
    assert RESULT_IP_BANNED_NOW not in history[1]


async def test_ban_entry_is_never_folded_into(
    hass: HomeAssistant, app: web.Application
) -> None:
    """A websocket can still fail auth after the ban; it gets its own entry."""
    await http_ban.process_wrong_login(_request(app))
    await http_ban.process_wrong_login(_request(app))
    await http_ban.process_wrong_login(_request(app))
    await hass.async_block_till_done()

    history = hass.data[DOMAIN].history
    assert [entry.get(RESULT_IP_BANNED_NOW, False) for entry in history] == [
        False,
        True,
        False,
    ]
    assert history[1]["count"] == 1


async def test_websocket_patch_also_marks_ban(
    hass: HomeAssistant, app: web.Application, events: list[dict[str, Any]]
) -> None:
    await websocket_auth.process_wrong_login(_request(app))
    await websocket_auth.process_wrong_login(_request(app))
    await hass.async_block_till_done()

    assert [event["reason"] for event in events] == [
        RESULT_REQUEST_REJECTED,
        RESULT_IP_BANNED_NOW,
    ]


async def test_already_banned_is_not_a_new_ban(
    hass: HomeAssistant, app: web.Application, events: list[dict[str, Any]]
) -> None:
    """A websocket can fail auth after a ban from another connection."""
    await app[http_ban.KEY_BAN_MANAGER].async_add_ban(ip_address(ATTACKER))

    await http_ban.process_wrong_login(_request(app))
    await hass.async_block_till_done()

    assert events[0]["reason"] == RESULT_REQUEST_REJECTED
    assert "attempts" not in events[0]


async def test_ban_disabled_reports_plain_rejection(
    hass: HomeAssistant, events: list[dict[str, Any]]
) -> None:
    """Without setup_bans core never bans; the event is as before 0.11.0."""
    bare = web.Application()
    bare[KEY_HASS] = hass

    for _ in range(3):
        await http_ban.process_wrong_login(_request(bare))
    await hass.async_block_till_done()

    assert {event["reason"] for event in events} == {RESULT_REQUEST_REJECTED}
    assert hass.data[DOMAIN].history[0]["count"] == 3


async def test_login_flow_wrong_password_below_threshold_is_not_reported(
    hass: HomeAssistant, app: web.Application, events: list[dict[str, Any]]
) -> None:
    """The wrong password is already login_detail_failed - no second report."""
    data = hass.data[DOMAIN]
    data.history = [_failed_login()]

    await auth_login_flow.process_wrong_login(_login_flow_request(app))
    await hass.async_block_till_done()

    assert events == []
    assert app[http_ban.KEY_FAILED_LOGIN_ATTEMPTS][ip_address(ATTACKER)] == 1
    assert len(data.history) == 1
    assert RESULT_IP_BANNED_NOW not in data.history[0]


async def test_login_flow_ban_marks_the_failed_login(
    hass: HomeAssistant, app: web.Application, events: list[dict[str, Any]]
) -> None:
    data = hass.data[DOMAIN]
    data.history = [_failed_login(flow_id="first")]
    await auth_login_flow.process_wrong_login(_login_flow_request(app, flow_id="first"))
    data.history.insert(0, _failed_login())
    await auth_login_flow.process_wrong_login(_login_flow_request(app))
    await hass.async_block_till_done()

    assert ip_address(ATTACKER) in app[http_ban.KEY_BAN_MANAGER].ip_bans_lookup
    assert len(events) == 1
    assert events[0]["reason"] == RESULT_IP_BANNED_NOW
    assert events[0]["endpoint"] == "/auth/login_flow"
    assert events[0]["flow_id"] == FLOW_ID
    assert events[0]["username"] is None
    assert (events[0]["attempts"], events[0]["threshold"]) == (2, 2)

    # One entry per attempt: the second attempt's own entry is marked.
    assert len(data.history) == 2
    assert data.history[0][RESULT_IP_BANNED_NOW] is True
    assert data.history[0]["result"] == "invalid_auth"
    assert data.history[0]["username"] == "probe-admin"
    assert (data.history[0]["attempts"], data.history[0]["threshold"]) == (2, 2)
    assert RESULT_IP_BANNED_NOW not in data.history[1]


async def test_login_flow_ban_without_its_entry_gets_one(
    hass: HomeAssistant, app: web.Application, events: list[dict[str, Any]]
) -> None:
    app[http_ban.KEY_FAILED_LOGIN_ATTEMPTS][ip_address(ATTACKER)] = 1

    await auth_login_flow.process_wrong_login(_login_flow_request(app))
    await hass.async_block_till_done()

    history = hass.data[DOMAIN].history
    assert len(events) == 1
    assert len(history) == 1
    assert history[0][RESULT_IP_BANNED_NOW] is True
    assert history[0]["result"] == RESULT_REQUEST_REJECTED
    assert history[0]["endpoint"] == "/auth/login_flow"
    assert history[0]["flow_id"] == FLOW_ID


async def test_revert_restores_login_flow(hass: HomeAssistant) -> None:
    ours = auth_login_flow.process_wrong_login
    _PATCH.revert()
    try:
        assert auth_login_flow.process_wrong_login is http_ban.process_wrong_login
        assert auth_login_flow.process_wrong_login is not ours
    finally:
        _PATCH.apply(hass)


async def test_sensor_shows_banned_per_ip(
    hass: HomeAssistant, app: web.Application
) -> None:
    await app[http_ban.KEY_BAN_MANAGER].async_add_ban(ip_address(ATTACKER))
    hass.http = _Http(app)

    data = hass.data[DOMAIN]
    now = dt_util.utcnow().isoformat()
    data.history = [
        {"timestamp": now, "result": "invalid_auth", "ip_address": ATTACKER},
        {"timestamp": now, "result": "ok", "ip_address": OTHER},
    ]
    sensor = LoginUniqueIpSensor(data)
    sensor.hass = hass

    rows = sensor.extra_state_attributes["addresses"]
    assert rows[ATTACKER]["banned"] is True
    assert rows[OTHER]["banned"] is False


async def test_sensor_banned_false_without_http(hass: HomeAssistant) -> None:
    """No http server, or IP banning off: false, never an exception."""
    hass.http = None
    data = hass.data[DOMAIN]
    data.history = [
        {
            "timestamp": dt_util.utcnow().isoformat(),
            "result": "invalid_auth",
            "ip_address": ATTACKER,
        }
    ]
    sensor = LoginUniqueIpSensor(data)
    sensor.hass = hass

    assert sensor.extra_state_attributes["addresses"][ATTACKER]["banned"] is False


def test_ban_fields_only_on_ban_entry() -> None:
    ban = {
        "result": RESULT_REQUEST_REJECTED,
        RESULT_IP_BANNED_NOW: True,
        "attempts": 5,
        "threshold": 5,
    }
    plain = {"result": RESULT_REQUEST_REJECTED}

    assert _as_text(ban)[RESULT_IP_BANNED_NOW] is True
    assert (_as_text(ban)["attempts"], _as_text(ban)["threshold"]) == (5, 5)
    assert "attempts" not in _as_text(plain)
    assert as_log_entry(ban)[RESULT_IP_BANNED_NOW] is True
    assert as_log_entry(ban)["type"] == "auth.request_rejected"
    assert RESULT_IP_BANNED_NOW not in as_log_entry(plain)
