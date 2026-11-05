"""The login flow patch against core's real auth classes (0.11.1).

What broke on 2026.11 was not behaviour but a name: the 2FA steps moved from
`LoginFlow` to a new `MfaLoginFlow`, so applying the patch raised
AttributeError and the integration never loaded. Nothing in the static checks
could see that, and the acceptance test only runs by hand - so these tests
apply the real patch to core's real classes and drive a real login flow
through it.

They need Home Assistant installed, but not
pytest-homeassistant-custom-component: the flow is driven directly, with a
stub auth provider and auth manager, so no running Home Assistant is needed.
Without Home Assistant the module is skipped.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("homeassistant")

from homeassistant.auth.models import Credentials, User
from homeassistant.auth.providers import LoginFlow
from homeassistant.auth.providers.homeassistant import HassLoginFlow
from homeassistant.data_entry_flow import FlowResultType

from custom_components.login_detail import _PATCH, MfaLoginFlow
from custom_components.login_detail.const import RESULT_MFA_PENDING

USERNAME = "someone"
FLOW_ID = "0123456789abcdef"
IP = "203.0.113.7"


class _Bus:
    def async_fire(self, *args: Any, **kwargs: Any) -> None:
        """Swallow the event; _report is stubbed out in these tests."""


class _Hass:
    """Enough of a Home Assistant for the patch to close over."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.bus = _Bus()
        # LoginFlow.__init__ reads it; the flow tests put a _Manager here.
        self.auth: Any = None


class _Manager:
    """The parts of core's auth manager MfaLoginFlow.async_finish calls."""

    def __init__(self, user: User, modules: dict[str, str]) -> None:
        self._user = user
        self._modules = modules

    async def async_get_user_by_credentials(self, credentials: Any) -> User:
        return self._user

    async def async_get_enabled_mfa(self, user: User) -> dict[str, str]:
        return self._modules


class _Provider:
    """The parts of core's auth provider MfaLoginFlow.async_finish calls."""

    def __init__(self, hass: _Hass, credentials: Credentials) -> None:
        self.hass = hass
        self._credentials = credentials

    async def async_get_or_create_credentials(self, flow_result: Any) -> Credentials:
        return self._credentials


def _flow(hass: _Hass, modules: dict[str, str]) -> LoginFlow:
    """A real HassLoginFlow wired to the stubs, ready for async_finish."""
    user = User(name=USERNAME, perm_lookup=None, groups=[])
    credentials = Credentials(
        auth_provider_type="homeassistant",
        auth_provider_id=None,
        data={"username": USERNAME},
        is_new=False,
    )
    hass.auth = _Manager(user, modules)
    provider = _Provider(hass, credentials)
    flow = HassLoginFlow(provider)  # type: ignore[arg-type]
    flow.hass = hass  # type: ignore[assignment]
    flow.flow_id = FLOW_ID
    flow.handler = ("homeassistant", None)  # type: ignore[assignment]
    flow.context = {"ip_address": IP}  # type: ignore[assignment]
    flow.init_data = None
    return flow


@pytest.fixture
def reports(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Record what the patch reports, instead of storing and firing it."""
    recorded: list[tuple[Any, ...]] = []

    def _record(hass: Any, result: str, *args: Any, **kwargs: Any) -> None:
        recorded.append((result, *args))

    monkeypatch.setattr("custom_components.login_detail._report", _record)
    return recorded


@pytest.fixture
def patched() -> Any:
    """The patch applied to core's real classes, and taken off again."""
    hass = _Hass()
    _PATCH.apply(hass)  # type: ignore[arg-type]
    yield hass
    _PATCH.revert()
    assert not _PATCH.applied


def test_mfa_steps_are_where_the_patch_looks_for_them() -> None:
    """The regression that 2026.11 caused: the steps moved off LoginFlow.

    `MfaLoginFlow` is core's class from 2026.11 and `LoginFlow` itself on
    older cores. Either way both methods the patch wraps must be on it.
    """
    assert hasattr(MfaLoginFlow, "async_step_mfa")
    assert hasattr(MfaLoginFlow, "async_finish")
    assert issubclass(MfaLoginFlow, LoginFlow)
    assert issubclass(HassLoginFlow, MfaLoginFlow)


def test_apply_and_revert(patched: _Hass) -> None:
    """Applying puts the wrappers on, reverting takes core's own back."""
    assert _PATCH.applied
    assert MfaLoginFlow.async_step_mfa.__name__ == "async_step_mfa"
    assert HassLoginFlow.async_step_init.__module__ == "custom_components.login_detail"
    assert MfaLoginFlow.async_step_mfa.__module__ == "custom_components.login_detail"
    if MfaLoginFlow is not LoginFlow:
        assert MfaLoginFlow.async_finish.__module__ == "custom_components.login_detail"
    else:
        # 2026.10 and older: core's own async_finish has to be left alone.
        # Wrapping it there reports as soon as the password is accepted,
        # which is the double-report fixed in 0.3.0.
        assert MfaLoginFlow.async_finish.__module__.startswith("homeassistant.")


def test_revert_restores_core(patched: _Hass) -> None:
    """After revert nothing of ours is left on core's classes."""
    _PATCH.revert()
    assert not _PATCH.applied
    assert HassLoginFlow.async_step_init.__module__.startswith("homeassistant.")
    assert MfaLoginFlow.async_step_mfa.__module__.startswith("homeassistant.")
    assert MfaLoginFlow.async_finish.__module__.startswith("homeassistant.")
    _PATCH.apply(_Hass())  # type: ignore[arg-type]


@pytest.mark.skipif(
    MfaLoginFlow is LoginFlow, reason="async_finish only sees 2FA from 2026.11"
)
def test_password_correct_and_2fa_enabled_is_mfa_pending(
    patched: _Hass, reports: list[tuple[Any, ...]]
) -> None:
    """Core returns a form from async_finish: password correct, 2FA missing."""
    flow = _flow(patched, {"totp": "Authenticator app", "notify": "Notify"})
    result = asyncio.run(flow.async_finish({"username": USERNAME}))
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_mfa_module"
    assert [report[0] for report in reports] == [RESULT_MFA_PENDING]
    assert reports[0][1] == USERNAME


@pytest.mark.skipif(
    MfaLoginFlow is LoginFlow, reason="async_finish only sees 2FA from 2026.11"
)
def test_password_correct_without_2fa_reports_nothing_yet(
    patched: _Hass, reports: list[tuple[Any, ...]]
) -> None:
    """No 2FA module: the entry is created, and `ok` is still left to
    async_finish_flow - reporting here would be the 0.3.0 double-report."""
    flow = _flow(patched, {})
    result = asyncio.run(flow.async_finish({"username": USERNAME}))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert reports == []
