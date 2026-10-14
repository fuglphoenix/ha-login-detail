"""Acceptance test probe 2 - the state AFTER the YAML line has been removed.

Runs in the same throwaway config that probe 1 left behind, but without
`login_detail:` in configuration.yaml. That is exactly the state an instance
is in after upgrading, once the line has been deleted.
"""

from __future__ import annotations

import asyncio
from ipaddress import ip_address
import json
import logging
import traceback
from typing import Any

from homeassistant.auth import AuthManagerFlowManager
from homeassistant.auth.providers import LoginFlow
from homeassistant.auth.providers.homeassistant import HassLoginFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

DOMAIN = "login_detail_probe2"
TARGET = "login_detail"
REPORT = "report2.json"

EVENT_FAILED = "login_detail_failed"
EVENT_OK = "login_detail_succeeded"

# Run B continues on run A's registry: either all new (0.8.0 and later) or all
# legacy entity IDs, depending on whether run A was seeded.
NEW_ENTITIES = {
    "sensor.login_latest_event",
    "sensor.login_ok_24h",
    "sensor.login_failed_24h",
    "sensor.login_ip_addresses_24h",
    "sensor.login_history",
    "sensor.login_active_sessions",
}
LEGACY_ENTITIES = {
    "sensor.login_seneste_haendelse",
    "sensor.login_ok_24t",
    "sensor.login_mislykkede_24t",
    "sensor.login_ip_adresser_24t",
    "sensor.login_historik",
    "sensor.login_aktive_sessioner",
}

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Start the probe once Home Assistant is up."""

    async def _started(event: Any) -> None:
        hass.async_create_task(_run(hass))

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started)
    return True


async def _run(hass: HomeAssistant) -> None:
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    try:
        await _checks(hass, check)
    except Exception:  # noqa: BLE001 - the report must be written regardless
        check("the probe ran to completion", False, traceback.format_exc())

    payload = {
        "passed": sum(1 for c in checks if c["ok"]),
        "total": len(checks),
        "checks": checks,
    }
    await hass.async_add_executor_job(_write, hass.config.path(REPORT), payload)
    _LOGGER.warning("LOGIN-DETAIL-PROBE2 %s/%s", payload["passed"], payload["total"])
    hass.async_create_task(hass.async_stop())


def _write(path: str, payload: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)


async def _checks(hass: HomeAssistant, check: Any) -> None:
    deadline = asyncio.get_running_loop().time() + 30
    entries: list[Any] = []
    while asyncio.get_running_loop().time() < deadline:
        entries = list(hass.config_entries.async_entries(TARGET))
        if entries and entries[0].state is ConfigEntryState.LOADED:
            break
        await asyncio.sleep(0.2)

    check("A1. the integration is loaded without the YAML line", bool(entries))
    if not entries:
        return
    check(
        "A2. there is still exactly one entry, and it is loaded",
        len(entries) == 1 and entries[0].state is ConfigEntryState.LOADED,
        {"count": len(entries), "state": str(entries[0].state)},
    )
    check(
        "A3. the entry was not recreated by an import",
        entries[0].source == "user",
        entries[0].source,
    )
    rows = er.async_entries_for_config_entry(er.async_get(hass), entries[0].entry_id)
    found = {row.entity_id for row in rows}
    expected = LEGACY_ENTITIES if found == LEGACY_ENTITIES else NEW_ENTITIES
    check(
        "A4. the six entity IDs are unchanged",
        found == expected,
        sorted(row.entity_id for row in rows),
    )
    check(
        "A5. all six have a state",
        all(hass.states.get(e) is not None for e in expected),
        {
            e: (hass.states.get(e).state if hass.states.get(e) else None)
            for e in expected
        },
    )
    patched = {
        "HassLoginFlow.async_step_init": "login_detail"
        in getattr(HassLoginFlow.async_step_init, "__module__", ""),
        "LoginFlow.async_step_mfa": "login_detail"
        in getattr(LoginFlow.async_step_mfa, "__module__", ""),
        "AuthManagerFlowManager.async_finish_flow": "login_detail"
        in getattr(AuthManagerFlowManager.async_finish_flow, "__module__", ""),
    }
    check("A6. the core methods are patched", all(patched.values()), patched)

    failed: list[dict[str, Any]] = []
    hass.bus.async_listen(EVENT_FAILED, lambda e: failed.append(dict(e.data)))
    flow = hass.auth.login_flow
    step = await flow.async_init(
        ("homeassistant", None), context={"ip_address": ip_address("203.0.113.20")}
    )
    step = await flow.async_configure(
        step["flow_id"], {"username": "probe", "password": "still-wrong"}
    )
    await hass.async_block_till_done()
    check(
        "A7. a failed login attempt is still logged with username and IP",
        len(failed) == 1
        and failed[0].get("username") == "probe"
        and failed[0].get("ip_address") == "203.0.113.20",
        failed,
    )
    check(
        "A8. the earlier history survived the restart",
        len(hass.data[TARGET].history) > 1,
        len(hass.data[TARGET].history),
    )
    second = await hass.config_entries.flow.async_init(
        TARGET, context={"source": "user"}
    )
    check(
        "A9. it cannot be added twice",
        second["type"] is FlowResultType.ABORT
        and second["reason"] == "single_instance_allowed",
        {"type": str(second["type"]), "reason": second.get("reason")},
    )
