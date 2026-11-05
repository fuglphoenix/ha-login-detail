"""The /auth/token classification against core's real auth code (0.11.2).

What broke on 2026.11 was again not behaviour but a signature: core's
`indieauth.verify_client_id` grew two keyword arguments, so the call raised
TypeError, `classify` swallowed it as it must, and every rejection on
/auth/token came back as `invalid_auth_request` with no grant type, no client
ID and nobody named. The self-check read core's source, found every line it
looks for still in place, and reported `token_endpoint` green.

These tests run the real classification against core's real
`indieauth.verify_client_id` and a stub auth manager, so the same class of
break fails here. They need Home Assistant installed, but not
pytest-homeassistant-custom-component. Without Home Assistant the module is
skipped.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("homeassistant")

from custom_components.login_detail import rejection
from custom_components.login_detail.const import (
    REJECTED_CLIENT_ID_MISMATCH,
    REJECTED_INVALID_AUTHORIZATION_CODE,
    REJECTED_INVALID_CLIENT_ID,
    REJECTED_MALFORMED_CREDENTIALS,
    REJECTED_UNKNOWN_OR_REVOKED_TOKEN,
    REJECTED_UNSUPPORTED_GRANT_TYPE,
    REJECTED_USER_NOT_ALLOWED,
)

CLIENT_ID = "http://127.0.0.1:8123/"
OTHER_CLIENT_ID = "https://cast.home-assistant.io/"
REFRESH_TOKEN = "a-refresh-token-only-this-test-has"
USERNAME = "someone"


class _Request:
    """The two attributes core's client ID check reads from 2026.11."""

    scheme = "http"
    host = "127.0.0.1:8123"


class _User:
    def __init__(self) -> None:
        self.id = "user-id"
        self.credentials: list[Any] = []


class _RefreshToken:
    def __init__(self, client_id: str | None) -> None:
        self.id = "refresh-token-id"
        self.client_id = client_id
        self.client_name = None
        self.token_type = "normal"
        self.user = _User()
        self.credential = None


class _Auth:
    """Only the lookup the refresh grant makes."""

    def __init__(self, refresh_token: _RefreshToken | None) -> None:
        self._refresh_token = refresh_token

    def async_get_refresh_token_by_token(self, token: str) -> _RefreshToken | None:
        return self._refresh_token if token == REFRESH_TOKEN else None


class _Hass:
    def __init__(self, refresh_token: _RefreshToken | None = None) -> None:
        self.auth = _Auth(refresh_token)

    async def async_add_executor_job(self, target: Any, *args: Any) -> Any:
        return target(*args)


def _classify(hass: Any, **form: str) -> dict[str, Any]:
    """Classify a /auth/token form the way _async_report_rejected does."""
    return rejection.classify(hass, _Request(), "/auth/token", form=form)


@pytest.fixture(autouse=True)
def _features() -> Any:
    """`features` is module state - a test must not leave it switched off."""
    original = dict(rejection.features)
    yield
    rejection.features.update(original)


def test_client_id_check_is_called_the_way_core_wants_it() -> None:
    """The 2026.11 regression: core's signature changed under the call.

    `_verify_client_id` has to accept the client ID core accepts, on both
    signatures - not raise, and not reject everything.
    """
    assert rejection._verify_client_id(_Request(), CLIENT_ID) is True
    assert rejection._verify_client_id(_Request(), "not a client id") is False
    assert rejection._verify_client_id(_Request(), None) is False


def test_refresh_grant_with_an_unknown_token() -> None:
    """The case that lost everything on 2026.11: reason, grant type, client."""
    found = _classify(
        _Hass(),
        grant_type="refresh_token",
        client_id=CLIENT_ID,
        refresh_token="not-a-refresh-token",
    )
    assert found["reason"] == REJECTED_UNKNOWN_OR_REVOKED_TOKEN
    assert found["grant_type"] == "refresh_token"
    assert found["client_id"] == CLIENT_ID
    assert found["user_id"] is None
    assert found["username"] is None


def test_refresh_grant_without_a_refresh_token() -> None:
    found = _classify(_Hass(), grant_type="refresh_token", client_id=CLIENT_ID)
    assert found["reason"] == REJECTED_MALFORMED_CREDENTIALS
    assert found["client_id"] == CLIENT_ID


def test_refresh_grant_with_an_invalid_client_id() -> None:
    """An invalid client ID is never reported back - only that it was one."""
    found = _classify(
        _Hass(),
        grant_type="refresh_token",
        client_id="not a client id",
        refresh_token=REFRESH_TOKEN,
    )
    assert found["reason"] == REJECTED_INVALID_CLIENT_ID
    assert found["grant_type"] == "refresh_token"
    assert found["client_id"] is None


def test_refresh_grant_with_the_wrong_client_id_names_the_user() -> None:
    """A real refresh token proves whose it is, even sent by another client."""
    hass = _Hass(_RefreshToken(OTHER_CLIENT_ID))
    found = _classify(
        hass,
        grant_type="refresh_token",
        client_id=CLIENT_ID,
        refresh_token=REFRESH_TOKEN,
    )
    assert found["reason"] == REJECTED_CLIENT_ID_MISMATCH
    assert found["user_id"] == "user-id"
    assert found["token_type"] == "normal"
    # The client ID that was asked for, not the one the token belongs to.
    assert found["client_id"] == CLIENT_ID


def test_refresh_grant_of_a_user_core_will_not_let_in() -> None:
    hass = _Hass(_RefreshToken(CLIENT_ID))
    found = _classify(
        hass,
        grant_type="refresh_token",
        client_id=CLIENT_ID,
        refresh_token=REFRESH_TOKEN,
    )
    assert found["reason"] == REJECTED_USER_NOT_ALLOWED
    assert found["user_id"] == "user-id"


def test_authorization_code_grant() -> None:
    found = _classify(
        _Hass(), grant_type="authorization_code", client_id=CLIENT_ID, code="made-up"
    )
    assert found["reason"] == REJECTED_INVALID_AUTHORIZATION_CODE
    assert found["grant_type"] == "authorization_code"
    assert found["client_id"] == CLIENT_ID


def test_unknown_grant_type_is_not_reported_as_typed() -> None:
    found = _classify(_Hass(), grant_type="password")
    assert found["reason"] == REJECTED_UNSUPPORTED_GRANT_TYPE
    assert found["grant_type"] == "other"
    assert found["client_id"] is None


def test_self_check_dry_run_passes_against_this_core() -> None:
    """The dry run must agree with the real classification on this core."""
    assert rejection._token_endpoint_dry_run(_Hass()) is True


def test_self_check_fails_when_the_client_id_check_changes_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the dry run: a signature change must not stay green.

    Core's source is untouched here, so the source check still passes - just
    as it did on 2026.11. Only running the classification finds it.
    """

    def _moved(client_id: str, *, request_url: str) -> bool:
        raise AssertionError("called with the arguments of an older core")

    monkeypatch.setattr(rejection.indieauth, "verify_client_id", _moved)
    hass = _Hass()
    assert rejection._token_endpoint_ok(hass) is True
    # The dry run itself raises, which is how the feature is switched off.
    assert rejection._passes(rejection._token_endpoint_dry_run, hass) is False

    found = asyncio.run(rejection.async_self_check(hass))
    assert found[rejection.FEATURE_TOKEN_ENDPOINT] is False
    assert rejection.features[rejection.FEATURE_TOKEN_ENDPOINT] is False
    # Switched off means the form is not read at all, and /auth/token falls
    # back to what it reported before 0.10.0.
    assert asyncio.run(rejection.token_form(object())) is None
    monkeypatch.undo()
    assert asyncio.run(rejection.async_self_check(hass))[
        rejection.FEATURE_TOKEN_ENDPOINT
    ]


def test_classify_never_raises_even_when_core_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever happens, a rejected request is still reported."""

    def _gone(*args: Any, **kwargs: Any) -> bool:
        raise TypeError("verify_client_id() got an unexpected keyword argument")

    monkeypatch.setattr(rejection.indieauth, "verify_client_id", _gone)
    found = _classify(
        _Hass(),
        grant_type="refresh_token",
        client_id=CLIENT_ID,
        refresh_token=REFRESH_TOKEN,
    )
    assert found["reason"] is None
    assert found["user_id"] is None
