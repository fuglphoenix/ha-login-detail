"""Acceptance test probe - runs inside a throwaway Home Assistant and reports.

The probe is NOT part of login_detail and is never installed on a real
instance. It starts when Home Assistant is up, runs the checks below against a
real AuthManager and a real config entry setup, writes the result to
report.json and shuts the instance down again.
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
from homeassistant.components.http import ban as http_ban
from homeassistant.components.websocket_api import auth as websocket_auth
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er, translation
from homeassistant.helpers.typing import ConfigType

DOMAIN = "login_detail_probe"
TARGET = "login_detail"
REPORT = "report.json"

EVENT_FAILED = "login_detail_failed"
EVENT_OK = "login_detail_succeeded"

EXPECTED_ENTITIES = {
    "sensor.login_seneste_haendelse": "login_detail_latest",
    "sensor.login_ok_24t": "login_detail_ok_24h",
    "sensor.login_mislykkede_24t": "login_detail_failed_24h",
    "sensor.login_ip_adresser_24t": "login_detail_unique_ip_24h",
    "sensor.login_historik": "login_detail_history",
    "sensor.login_aktive_sessioner": "login_detail_sessions",
}

# The test user "probe" and its password only exist in the throwaway config the
# test creates itself.
PASSWORD = "correct-horse-battery"

_LOGGER = logging.getLogger(__name__)


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def check(self, name: str, ok: bool, detail: Any = None) -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c["ok"])

    @property
    def total(self) -> int:
        return len(self.checks)


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

    payload = {
        "passed": report.passed,
        "total": report.total,
        "checks": report.checks,
    }
    await hass.async_add_executor_job(_write, hass.config.path(REPORT), payload)
    _LOGGER.warning("LOGIN-DETAIL-PROBE %s/%s", report.passed, report.total)
    hass.async_create_task(hass.async_stop())


def _write(path: str, payload: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)


def _entries(hass: HomeAssistant) -> list[Any]:
    return list(hass.config_entries.async_entries(TARGET))


async def _wait_for_entry(hass: HomeAssistant, timeout: float = 30.0) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        entries = _entries(hass)
        if entries and entries[0].state is ConfigEntryState.LOADED:
            return entries[0]
        await asyncio.sleep(0.2)
    entries = _entries(hass)
    return entries[0] if entries else None


class Capture:
    """Collects login_detail events."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.failed: list[dict[str, Any]] = []
        self.ok: list[dict[str, Any]] = []
        self._unsub = [
            hass.bus.async_listen(EVENT_FAILED, self._on_failed),
            hass.bus.async_listen(EVENT_OK, self._on_ok),
        ]

    def _on_failed(self, event: Any) -> None:
        self.failed.append(dict(event.data))

    def _on_ok(self, event: Any) -> None:
        self.ok.append(dict(event.data))

    def reset(self) -> None:
        self.failed.clear()
        self.ok.clear()


async def _login(
    hass: HomeAssistant,
    username: str,
    password: str,
    ip: str,
    code: str | None = None,
) -> Any:
    flow = hass.auth.login_flow
    result = await flow.async_init(
        ("homeassistant", None), context={"ip_address": ip_address(ip)}
    )
    result = await flow.async_configure(
        result["flow_id"], {"username": username, "password": password}
    )
    if code is not None and result["type"] is FlowResultType.FORM:
        result = await flow.async_configure(result["flow_id"], {"code": code})
    await hass.async_block_till_done()
    return result


def _is_patched() -> dict[str, bool]:
    """Which core methods currently carry login_detail's monkeypatch."""
    return {
        "HassLoginFlow.async_step_init": "login_detail"
        in getattr(HassLoginFlow.async_step_init, "__module__", ""),
        "LoginFlow.async_step_mfa": "login_detail"
        in getattr(LoginFlow.async_step_mfa, "__module__", ""),
        "AuthManagerFlowManager.async_finish_flow": "login_detail"
        in getattr(AuthManagerFlowManager.async_finish_flow, "__module__", ""),
        "ban.process_wrong_login": "login_detail"
        in getattr(http_ban.process_wrong_login, "__module__", ""),
        "websocket_api.auth.process_wrong_login": "login_detail"
        in getattr(websocket_auth.process_wrong_login, "__module__", ""),
        "AuthPhase.async_handle": "login_detail"
        in getattr(websocket_auth.AuthPhase.async_handle, "__module__", ""),
    }


def _unpatch_mechanics() -> tuple[bool, Any]:
    """Both cases of reverting, exercised on toy classes.

    In HA 2026.10.1 all three patched methods are the classes' own, so the
    delattr branch is never hit by the checks above. It must still work if a
    future core version inherits one of them.
    """
    from custom_components.login_detail import _CorePatch

    class _Base:
        def greet(self) -> str:
            return "base"

    class _Sub(_Base):
        pass

    def _wrapped(self: Any) -> str:
        return "patched"

    detail: dict[str, Any] = {}

    # 1) inherited method: must be removed again, not copied onto the subclass
    patch = _CorePatch()
    patch._install(_Sub, "greet", _wrapped)
    detail["inherited_patched"] = _Sub().greet() == "patched"
    patch.revert()
    detail["inherited_cleaned"] = (
        _Sub().greet() == "base" and "greet" not in _Sub.__dict__
    )

    # 2) the class's own method: the original must be written back
    patch = _CorePatch()
    patch._install(_Base, "greet", _wrapped)
    patch.revert()
    detail["own_restored"] = _Base().greet() == "base" and "greet" in _Base.__dict__

    # 3) if someone else has patched on top of us, we leave them alone
    def _other(self: Any) -> str:
        return "third-party"

    patch = _CorePatch()
    patch._install(_Base, "greet", _wrapped)
    _Base.greet = _other  # type: ignore[method-assign]
    patch.revert()
    detail["foreign_patch_kept"] = _Base().greet() == "third-party"

    return all(detail.values()), detail


async def _checks(hass: HomeAssistant, report: Report) -> None:
    capture = Capture(hass)

    # ---- 1. The YAML line has been imported into a config entry -----------
    entry = await _wait_for_entry(hass)
    report.check("1. config entry created from the YAML line", entry is not None)
    if entry is None:
        return
    report.check(
        "2. the entry's source is import", entry.source == "import", entry.source
    )
    report.check(
        "3. the entry is loaded",
        entry.state is ConfigEntryState.LOADED,
        str(entry.state),
    )
    report.check("4. only one entry", len(_entries(hass)) == 1, len(_entries(hass)))

    # ---- 2. The entities survive the move from YAML ----------------------
    registry = er.async_get(hass)
    rows = er.async_entries_for_config_entry(registry, entry.entry_id)
    found = {row.entity_id: row.unique_id for row in rows}
    report.check(
        "5. exactly the six known entities are linked to the entry",
        found == EXPECTED_ENTITIES,
        {"found": found},
    )
    report.check(
        "6. all six entities have a state",
        all(hass.states.get(e) is not None for e in EXPECTED_ENTITIES),
        {
            e: (hass.states.get(e).state if hass.states.get(e) else None)
            for e in EXPECTED_ENTITIES
        },
    )

    # ---- 3. The monkeypatch is applied -----------------------------------
    report.check(
        "7. the core methods are patched", all(_is_patched().values()), _is_patched()
    )

    # ---- 4. Real logins --------------------------------------------------
    provider = hass.auth.auth_providers[0]
    await provider.async_initialize()
    await provider.async_add_auth("probe", PASSWORD)
    credentials = await provider.async_get_or_create_credentials({"username": "probe"})
    user = await hass.auth.async_get_or_create_user(credentials)

    capture.reset()
    result = await _login(hass, "probe", "wrong", "203.0.113.5")
    report.check(
        "8. wrong password: one failed event with username and IP",
        len(capture.failed) == 1
        and capture.failed[0].get("username") == "probe"
        and capture.failed[0].get("ip_address") == "203.0.113.5"
        and capture.failed[0].get("reason") == "invalid_auth",
        capture.failed,
    )
    report.check(
        "9. the password does not appear in the event",
        all("wrong" not in json.dumps(e) for e in capture.failed),
        None,
    )

    capture.reset()
    result = await _login(hass, "probe", PASSWORD, "203.0.113.6")
    report.check(
        "10. correct login without 2FA: the flow completes",
        result["type"] is FlowResultType.CREATE_ENTRY,
        str(result["type"]),
    )
    report.check(
        "11. a correct login gives exactly one ok and no failed",
        len(capture.ok) == 1 and not capture.failed,
        {"ok": capture.ok, "failed": capture.failed},
    )

    # ---- 5. 2FA: ok only once the code has been entered ------------------
    import pyotp

    secret = pyotp.random_base32()
    totp_module = hass.auth.auth_mfa_modules[0] if hass.auth.auth_mfa_modules else None
    if totp_module is None:
        report.check("12. totp module loaded", False, "no mfa modules")
    else:
        await totp_module.async_setup_user(user.id, {"secret": secret})
        capture.reset()
        flow = hass.auth.login_flow
        step = await flow.async_init(
            ("homeassistant", None),
            context={"ip_address": ip_address("203.0.113.7")},
        )
        step = await flow.async_configure(
            step["flow_id"], {"username": "probe", "password": PASSWORD}
        )
        await hass.async_block_till_done()
        report.check(
            "12. password correct + 2FA required gives mfa_pending, not ok",
            not capture.ok
            and len(capture.failed) == 1
            and capture.failed[0].get("reason") == "mfa_pending",
            {"ok": capture.ok, "failed": capture.failed, "step": str(step["type"])},
        )
        code = pyotp.TOTP(secret).now()
        step = await flow.async_configure(step["flow_id"], {"code": code})
        await hass.async_block_till_done()
        report.check(
            "13. after a correct 2FA code: exactly one ok",
            step["type"] is FlowResultType.CREATE_ENTRY and len(capture.ok) == 1,
            {"ok": capture.ok, "step": str(step["type"])},
        )
        history = hass.data[TARGET].history
        same_flow = [h for h in history if h.get("flow_id") == step["flow_id"]]
        report.check(
            "14. one login gives one history entry",
            len(same_flow) == 1 and same_flow[0].get("result") == "ok",
            same_flow,
        )
        report.check(
            "15. the TOTP code appears nowhere",
            code not in json.dumps(history)
            and all(code not in json.dumps(e) for e in capture.ok),
            None,
        )
        await totp_module.async_depose_user(user.id)

    # ---- 6. Reload from the UI -------------------------------------------
    before_reload = len(hass.data[TARGET].history)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    report.check(
        "15b. the history survives an immediate reload",
        len(hass.data[TARGET].history) >= before_reload,
        {"before": before_reload, "after": len(hass.data[TARGET].history)},
    )
    report.check(
        "16. the entry can be reloaded",
        _entries(hass)[0].state is ConfigEntryState.LOADED,
        str(_entries(hass)[0].state),
    )
    report.check(
        "17. the monkeypatch is still applied after reload",
        all(_is_patched().values()),
        _is_patched(),
    )
    capture.reset()
    await _login(hass, "probe", "wrong-again", "203.0.113.8")
    report.check(
        "18. logging works after reload",
        len(capture.failed) == 1
        and capture.failed[0].get("ip_address") == "203.0.113.8",
        capture.failed,
    )
    rows = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    report.check(
        "19. the entities keep their IDs after reload",
        {row.entity_id for row in rows} == set(EXPECTED_ENTITIES),
        sorted(row.entity_id for row in rows),
    )

    # ---- 7. Removal from the UI also removes the monkeypatch -------------
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    report.check("20. the entry can be removed", not _entries(hass), _entries(hass))
    report.check(
        "21. the core methods are restored after removal",
        not any(_is_patched().values()),
        _is_patched(),
    )
    qualnames = {
        "HassLoginFlow.async_step_init": HassLoginFlow.async_step_init.__qualname__,
        "LoginFlow.async_step_mfa": LoginFlow.async_step_mfa.__qualname__,
        "AuthManagerFlowManager.async_finish_flow": (
            AuthManagerFlowManager.async_finish_flow.__qualname__
        ),
    }
    report.check(
        "22. core's own methods are back, with their own names",
        all(name == value for name, value in qualnames.items()),
        qualnames,
    )
    report.check(
        "22b. reverting cleans up whether the method was inherited or own",
        *_unpatch_mechanics(),
    )
    capture.reset()
    await _login(hass, "probe", "wrong-after-removal", "203.0.113.9")
    report.check(
        "23. login still works after removal, without events",
        not capture.failed and not capture.ok,
        {"failed": capture.failed, "ok": capture.ok},
    )
    report.check(
        "24. the entities are gone after removal",
        all(hass.states.get(e) is None for e in EXPECTED_ENTITIES),
        [e for e in EXPECTED_ENTITIES if hass.states.get(e) is not None],
    )

    # ---- 8. Setup from the UI --------------------------------------------
    flow_result = await hass.config_entries.flow.async_init(
        TARGET, context={"source": "user"}
    )
    report.check(
        "25. the UI flow shows a form",
        flow_result["type"] is FlowResultType.FORM and flow_result["step_id"] == "user",
        {"type": str(flow_result["type"]), "step": flow_result.get("step_id")},
    )
    flow_result = await hass.config_entries.flow.async_configure(
        flow_result["flow_id"], {}
    )
    await hass.async_block_till_done()
    report.check(
        "26. the UI flow creates the entry",
        flow_result["type"] is FlowResultType.CREATE_ENTRY,
        str(flow_result["type"]),
    )
    new_entry = _entries(hass)[0] if _entries(hass) else None
    report.check(
        "27. the entry is loaded and has source user",
        new_entry is not None
        and new_entry.state is ConfigEntryState.LOADED
        and new_entry.source == "user",
        {
            "source": getattr(new_entry, "source", None),
            "state": str(getattr(new_entry, "state", None)),
        },
    )
    rows = er.async_entries_for_config_entry(er.async_get(hass), new_entry.entry_id)
    report.check(
        "28. the same six entity IDs come back",
        {row.entity_id for row in rows} == set(EXPECTED_ENTITIES),
        sorted(row.entity_id for row in rows),
    )
    capture.reset()
    await _login(hass, "probe", "wrong-at-the-end", "203.0.113.10")
    report.check(
        "29. logging works again after UI setup",
        len(capture.failed) == 1
        and capture.failed[0].get("ip_address") == "203.0.113.10",
        capture.failed,
    )
    report.check(
        "30. the earlier history survived",
        len(hass.data[TARGET].history) > 1,
        len(hass.data[TARGET].history),
    )

    # ---- 9. Only one instance allowed ------------------------------------
    second = await hass.config_entries.flow.async_init(
        TARGET, context={"source": "user"}
    )
    report.check(
        "31. a second attempt is rejected",
        second["type"] is FlowResultType.ABORT
        and second["reason"] == "single_instance_allowed",
        {"type": str(second["type"]), "reason": second.get("reason")},
    )

    # ---- 10. The UI texts -------------------------------------------------
    for language in ("da", "en"):
        texts = await translation.async_get_translations(
            hass, language, "config", {TARGET}
        )
        key = f"component.{TARGET}.config.step.user.description"
        report.check(
            f"33. {language} text for the setup dialog exists",
            bool(texts.get(key)),
            texts.get(key),
        )
