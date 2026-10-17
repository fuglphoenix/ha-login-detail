"""Why a request was rejected, and by whom - if that can be proven (0.7.1).

Core rejects a request with one message for every cause. This module tells
the causes apart after core has rejected the request, from what the request
carries:

  no_credentials            no Authorization header, no signed path, no token
                            in the websocket auth message - for example an MCP
                            client's first, unauthenticated call
  malformed_credentials     an Authorization header that is not
                            `Bearer <token>`, a websocket auth message
                            without a usable access token, or a token that
                            is not a JWT at all (0.10.0)
  invalid_signed_path      an `authSig` signed path that core did not accept
  expired_token *           a token signed by a refresh token that still
                            exists, but past its expiry
  user_not_allowed *        a token that is validly signed and not expired,
                            for a user who is inactive or local only and
                            connected from outside
  unknown_or_revoked_token  a token whose refresh token does not exist (any
                            more), or whose signature does not match
  not_admin *               a request core authenticated, rejected by the
                            endpoint because the user is not an administrator
                            (an MCP server set to require admin)
  permission_denied *       the same for an administrator - another permission
  invalid_auth_request      rejected by /auth/login_flow, or by /auth/token
                            when its form could not be read (see below)

/auth/token (0.10.0) is told apart from the form fields core has already
read - aiohttp keeps the parsed form on the request, so nothing is read from
the network again:

  invalid_client_id           the client_id is missing or not a valid URL
  unknown_or_revoked_token    refresh grant with a refresh token that does not
                              exist (any more)
  client_id_mismatch *        refresh grant with a real refresh token, sent
                              with another client_id than it was issued to
  user_not_allowed *          refresh grant with a real refresh token of a
                              user who is inactive or local only
  malformed_credentials       refresh grant without a refresh token
  invalid_authorization_code  authorization code grant that failed - the code
                              is unknown, expired or already used, the PKCE
                              verifier did not match, or the user was not
                              allowed. Core has already used the code up, so
                              these cannot be told apart afterwards.
  unsupported_grant_type      no grant_type, or one core does not know

The event also gets `grant_type` (only the two core knows, anything else as
"other") and the requested `client_id` - a public URL, not a secret. For a
refresh grant, possession of the refresh token is the proof: it is looked up
the way core looks it up (`async_get_refresh_token_by_token`, a constant-time
comparison), and only then is its user named.

Only the reasons marked * name a user, and only after the token's signature
has been checked against the `jwt_key` of the refresh token it names: by core
for a request it authenticated, here for one it did not. The expiry is the
only claim that is not enforced here - that is how an expired token can still
be proven to belong to someone. Nothing an unverified token claims is ever
reported.

The token itself is never stored, logged, compared to anything stored, or
returned - only what can be derived from it. All of this runs in memory after
core has already answered, with no I/O and no network calls, and it never
raises.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
import inspect
import logging
import time
from typing import Any

from aiohttp import hdrs
from homeassistant.auth import AuthManager
from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
from homeassistant.components.http.auth import SIGN_QUERY_PARAM
from homeassistant.components.http.const import (
    KEY_HASS_REFRESH_TOKEN_ID,
    KEY_HASS_USER,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.http import KEY_AUTHENTICATED
import jwt

from .const import (
    ENDPOINT_TOKEN,
    GRANT_TYPE_OTHER,
    GRANT_TYPES,
    HEADER_MAX_LENGTH,
    REJECTED_CLIENT_ID_MISMATCH,
    REJECTED_EXPIRED_TOKEN,
    REJECTED_INVALID_AUTH_REQUEST,
    REJECTED_INVALID_AUTHORIZATION_CODE,
    REJECTED_INVALID_CLIENT_ID,
    REJECTED_INVALID_SIGNED_PATH,
    REJECTED_MALFORMED_CREDENTIALS,
    REJECTED_NO_CREDENTIALS,
    REJECTED_NOT_ADMIN,
    REJECTED_PERMISSION_DENIED,
    REJECTED_UNKNOWN_OR_REVOKED_TOKEN,
    REJECTED_UNSUPPORTED_GRANT_TYPE,
    REJECTED_USER_NOT_ALLOWED,
)

try:  # private core API - without it, /auth/token is not told apart
    from homeassistant.components.auth import TokenView, indieauth
except ImportError:  # pragma: no cover - depends on the core version
    TokenView = indieauth = None

_LOGGER = logging.getLogger(__name__)

# Core does not decode a token larger than this (jwt_wrapper.MAX_TOKEN_SIZE),
# so a larger one cannot have been valid - and is not worth decoding.
MAX_TOKEN_LENGTH = 8192

# Core accepts a token this many seconds past its expiry.
EXPIRY_LEEWAY = 10

# The websocket sends its token in the auth message, not in a header, so the
# request that reaches process_wrong_login does not have it. The patched
# AuthPhase.async_handle puts it here for the duration of the auth step:
# the token, "" for an auth message with other credentials (api_password) or
# an empty token, and None for no credentials at all.
websocket_token: ContextVar[str | None] = ContextVar(
    "login_detail_websocket_token", default=None
)

# Fields a rejected request can be attributed with. None unless proven -
# except client_id on /auth/token, which is the one the request asked for.
ATTRIBUTION_FIELDS = ("user_id", "username", "client_id", "client_name", "token_type")

# What the self-check found at startup (see async_self_check). A feature
# whose core API has changed is switched off, and the integration falls back
# to what it reported before that feature existed, instead of guessing.
FEATURE_TOKEN_IDENTIFICATION = "token_identification"
FEATURE_TOKEN_ENDPOINT = "token_endpoint"
features: dict[str, bool] = {
    FEATURE_TOKEN_IDENTIFICATION: True,
    FEATURE_TOKEN_ENDPOINT: True,
}


def classify(
    hass: HomeAssistant,
    request: Any,
    endpoint: str | None,
    *,
    websocket: bool = False,
    form: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The reason a request was rejected and, if proven, who sent it.

    Returns `reason`, `grant_type` and the ATTRIBUTION_FIELDS. `form` is the
    form of a request to /auth/token, see token_form. Never raises: on
    anything unexpected the reason is None and nobody is named.
    """
    try:
        return _classify(hass, request, endpoint, websocket, form)
    except Exception:  # noqa: BLE001 - must never break request handling
        _LOGGER.debug("login_detail: could not classify a rejected request")
        return _found(None)


async def token_form(request: Any) -> Mapping[str, Any] | None:
    """The form a rejected /auth/token request was sent with, or None.

    Core's TokenView has already read and parsed it, and aiohttp keeps the
    result on the request, so `post()` returns that - it does not read from
    the network again. It is only looked at in memory, here and in
    _classify_token_endpoint, and never stored, logged or passed on.
    """
    if not features[FEATURE_TOKEN_ENDPOINT]:
        return None
    try:
        form = await request.post()
    except Exception:  # noqa: BLE001 - fall back to invalid_auth_request
        return None
    return form if isinstance(form, Mapping) else None


def _classify(
    hass: HomeAssistant,
    request: Any,
    endpoint: str | None,
    websocket: bool,
    form: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if endpoint == ENDPOINT_TOKEN and form is not None:
        return _classify_token_endpoint(hass, form)
    if endpoint and endpoint.startswith("/auth/"):
        # /auth/login_flow rejects on what is in the body, and the body is
        # not read here. /auth/token too, if its form could not be read.
        return _found(REJECTED_INVALID_AUTH_REQUEST)

    if websocket:
        token = websocket_token.get()
        if token is None:
            return _found(REJECTED_NO_CREDENTIALS)
        if not token:
            return _found(REJECTED_MALFORMED_CREDENTIALS)
        return _classify_token(hass, token)

    if request.get(KEY_AUTHENTICATED):
        # Core verified the token and let the request through; the endpoint
        # turned it away afterwards (exceptions.Unauthorized -> 401).
        user = request.get(KEY_HASS_USER)
        refresh_token_id = request.get(KEY_HASS_REFRESH_TOKEN_ID)
        refresh_token = (
            hass.auth.async_get_refresh_token(refresh_token_id)
            if isinstance(refresh_token_id, str)
            else None
        )
        reason = (
            REJECTED_PERMISSION_DENIED
            if getattr(user, "is_admin", False)
            else REJECTED_NOT_ADMIN
        )
        return _found(reason, user, refresh_token)

    header = request.headers.get(hdrs.AUTHORIZATION)
    if header is None:
        if SIGN_QUERY_PARAM in request.query_string:
            return _found(REJECTED_INVALID_SIGNED_PATH)
        return _found(REJECTED_NO_CREDENTIALS)
    scheme, _, token = header.partition(" ")
    if scheme != "Bearer" or not token:
        return _found(REJECTED_MALFORMED_CREDENTIALS)
    return _classify_token(hass, token)


def _classify_token(hass: HomeAssistant, token: str) -> dict[str, Any]:
    """A token core rejected: expired, not allowed, or not provably anyone's.

    The same check core makes in AuthManager.async_validate_access_token,
    except for the expiry: the unverified `iss` names a refresh token, and the
    token is only believed if it is signed with that refresh token's key.
    """
    if not features[FEATURE_TOKEN_IDENTIFICATION]:
        # The self-check found core's token format changed: report the
        # rejection without a reason, as before 0.7.1, rather than guess.
        return _found(None)
    if len(token) > MAX_TOKEN_LENGTH:
        return _found(REJECTED_UNKNOWN_OR_REVOKED_TOKEN)
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        # Not a JWT at all - a random string, a password, a cut-off token
        # (0.10.0; unknown_or_revoked_token before).
        return _found(REJECTED_MALFORMED_CREDENTIALS)
    try:
        issuer = unverified.get("iss")
        refresh_token = (
            hass.auth.async_get_refresh_token(issuer)
            if isinstance(issuer, str)
            else None
        )
        if refresh_token is None:
            return _found(REJECTED_UNKNOWN_OR_REVOKED_TOKEN)
        claims = jwt.decode(
            token,
            refresh_token.jwt_key,
            algorithms=["HS256"],
            issuer=refresh_token.id,
            options={"verify_exp": False, "require": ["iss", "iat", "exp"]},
        )
    except jwt.PyJWTError:
        return _found(REJECTED_UNKNOWN_OR_REVOKED_TOKEN)

    expires = claims.get("exp")
    if not isinstance(expires, (int, float)) or isinstance(expires, bool):
        return _found(REJECTED_UNKNOWN_OR_REVOKED_TOKEN)
    if time.time() > expires + EXPIRY_LEEWAY:
        return _found(REJECTED_EXPIRED_TOKEN, refresh_token.user, refresh_token)
    # Validly signed and still valid, yet core said no: the user is inactive,
    # or local only and connecting from outside.
    return _found(REJECTED_USER_NOT_ALLOWED, refresh_token.user, refresh_token)


def _classify_token_endpoint(
    hass: HomeAssistant, form: Mapping[str, Any]
) -> dict[str, Any]:
    """Why /auth/token turned the grant down - the same checks, in core's order.

    Mirrors TokenView in Home Assistant 2026.10.1. Only the grant type, the
    requested client ID and, once the refresh token is proven, its user and
    token type leave this function.
    """
    grant_type = form.get("grant_type")
    if grant_type not in GRANT_TYPES:
        return _found(REJECTED_UNSUPPORTED_GRANT_TYPE, grant_type=GRANT_TYPE_OTHER)
    client_id = form.get("client_id")
    valid_client_id = isinstance(client_id, str) and indieauth.verify_client_id(
        client_id
    )

    if grant_type == "authorization_code":
        if not valid_client_id:
            return _found(REJECTED_INVALID_CLIENT_ID, grant_type=grant_type)
        return _found(
            REJECTED_INVALID_AUTHORIZATION_CODE,
            grant_type=grant_type,
            client_id=client_id,
        )

    # A refresh grant may leave client_id out; core then compares with None.
    if client_id is not None and not valid_client_id:
        return _found(REJECTED_INVALID_CLIENT_ID, grant_type=grant_type)
    requested = client_id if valid_client_id else None
    token = form.get("refresh_token")
    if not isinstance(token, str):
        return _found(
            REJECTED_MALFORMED_CREDENTIALS, grant_type=grant_type, client_id=requested
        )
    refresh_token = hass.auth.async_get_refresh_token_by_token(token)
    if refresh_token is None:
        return _found(
            REJECTED_UNKNOWN_OR_REVOKED_TOKEN,
            grant_type=grant_type,
            client_id=requested,
        )
    reason = (
        REJECTED_CLIENT_ID_MISMATCH
        if refresh_token.client_id != client_id
        else REJECTED_USER_NOT_ALLOWED
    )
    return _found(
        reason,
        refresh_token.user,
        refresh_token,
        grant_type=grant_type,
        client_id=requested,
    )


def _found(
    reason: str | None,
    user: Any = None,
    refresh_token: Any = None,
    *,
    grant_type: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    """The result, with the user and client of a proven token filled in.

    `client_id` is the client ID a /auth/token request asked for. It is
    reported as requested, also when a proven refresh token belongs to
    another client - that is the mismatch.
    """
    result: dict[str, Any] = dict.fromkeys(ATTRIBUTION_FIELDS)
    result["reason"] = reason
    result["grant_type"] = grant_type
    if grant_type is not None:
        result["client_id"] = clean(client_id)
    if user is None:
        return result
    result["user_id"] = getattr(user, "id", None)
    result["username"] = _username_of(user, refresh_token)
    if refresh_token is not None:
        if grant_type is None:
            result["client_id"] = refresh_token.client_id
        result["token_type"] = refresh_token.token_type
        if refresh_token.token_type == TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN:
            # The name the user gave the token. A normal token's client_name
            # is not set; its client_id says what it is.
            result["client_name"] = refresh_token.client_name
    return result


def _username_of(user: Any, refresh_token: Any) -> str | None:
    """The username the user logs in with, from the auth store.

    It comes from the user's stored credentials - those the refresh token was
    created with first - so it is always a known user's username, never
    anything the client sent. A user without a username (a system user, or
    one who has never logged in) gives None.
    """
    credentials = [getattr(refresh_token, "credential", None)]
    credentials.extend(getattr(user, "credentials", None) or [])
    for credential in credentials:
        username = (getattr(credential, "data", None) or {}).get("username")
        if isinstance(username, str) and username:
            return username
    return None


def clean(value: Any) -> str | None:
    """Text set by the client, safe to store: cut, control characters replaced."""
    if not isinstance(value, str) or not value:
        return None
    return "".join(
        char if char.isprintable() else "\ufffd" for char in value[:HEADER_MAX_LENGTH]
    )


async def async_self_check(hass: HomeAssistant) -> dict[str, bool]:
    """Check that the private core API behind 0.7.1 and 0.10.0 is still there.

    Token identification relies on core's access tokens being HS256 JWTs whose
    `iss` is the refresh token ID, and on `async_get_refresh_token`. The
    /auth/token details rely on TokenView reading a form, wrapped by
    `log_invalid_auth`, checking grant_type, client_id and the refresh token
    in that order, and on `async_get_refresh_token_by_token`. Both are checked
    against core's own source, without creating a token. A feature that fails
    is switched off (see `features`), and a WARNING says so.
    """
    found = await hass.async_add_executor_job(_self_check, hass)
    features.update(found)
    return found


def _self_check(hass: HomeAssistant) -> dict[str, bool]:
    return {
        FEATURE_TOKEN_IDENTIFICATION: _passes(_token_identification_ok, hass),
        FEATURE_TOKEN_ENDPOINT: _passes(_token_endpoint_ok, hass),
    }


def _passes(check: Any, hass: HomeAssistant) -> bool:
    try:
        return bool(check(hass))
    except Exception:  # noqa: BLE001 - a failed check switches the feature off
        return False


def _token_identification_ok(hass: HomeAssistant) -> bool:
    source = inspect.getsource(AuthManager.async_create_access_token)
    return (
        callable(getattr(hass.auth, "async_get_refresh_token", None))
        and '"iss": refresh_token.id' in source
        and "refresh_token.jwt_key" in source
        and 'algorithm="HS256"' in source
    )


def _token_endpoint_ok(hass: HomeAssistant) -> bool:
    if TokenView is None or indieauth is None:
        return False
    source = inspect.getsource(TokenView)
    return (
        callable(getattr(hass.auth, "async_get_refresh_token_by_token", None))
        and callable(getattr(indieauth, "verify_client_id", None))
        and "log_invalid_auth" in TokenView.post.__qualname__
        and "await request.post()" in source
        and 'data.get("grant_type")' in source
        and 'data.get("client_id")' in source
        and 'data.get("refresh_token")' in source
        and "async_get_refresh_token_by_token(token)" in source
        and "refresh_token.client_id != client_id" in source
    )
