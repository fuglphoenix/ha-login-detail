"""Why a request was rejected, and by whom - if that can be proven (0.7.1).

Core rejects a request with one message for every cause. This module tells
the causes apart after core has rejected the request, from what the request
carries:

  no_credentials            no Authorization header, no signed path, no token
                            in the websocket auth message - for example an MCP
                            client's first, unauthenticated call
  malformed_credentials     an Authorization header that is not
                            `Bearer <token>`, or a websocket auth message
                            without a usable access token
  invalid_signed_path       an `authSig` signed path that core did not accept
  expired_token *           a token signed by a refresh token that still
                            exists, but past its expiry
  user_not_allowed *        a token that is validly signed and not expired,
                            for a user who is inactive or local only and
                            connected from outside
  unknown_or_revoked_token  a token whose refresh token does not exist (any
                            more), whose signature does not match, or that is
                            not a token at all
  not_admin *               a request core authenticated, rejected by the
                            endpoint because the user is not an administrator
                            (an MCP server set to require admin)
  permission_denied *       the same for an administrator - another permission
  invalid_auth_request      rejected by /auth/token or /auth/login_flow; the
                            request body is not read

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

from contextvars import ContextVar
import logging
import time
from typing import Any

from aiohttp import hdrs
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
    REJECTED_EXPIRED_TOKEN,
    REJECTED_INVALID_AUTH_REQUEST,
    REJECTED_INVALID_SIGNED_PATH,
    REJECTED_MALFORMED_CREDENTIALS,
    REJECTED_NO_CREDENTIALS,
    REJECTED_NOT_ADMIN,
    REJECTED_PERMISSION_DENIED,
    REJECTED_UNKNOWN_OR_REVOKED_TOKEN,
    REJECTED_USER_NOT_ALLOWED,
)

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

# Fields a rejected request can be attributed with. None unless proven.
ATTRIBUTION_FIELDS = ("user_id", "username", "client_id", "client_name", "token_type")


def classify(
    hass: HomeAssistant,
    request: Any,
    endpoint: str | None,
    *,
    websocket: bool = False,
) -> dict[str, Any]:
    """The reason a request was rejected and, if proven, who sent it.

    Returns `reason` and the ATTRIBUTION_FIELDS. Never raises: on anything
    unexpected the reason is None and nobody is named.
    """
    try:
        return _classify(hass, request, endpoint, websocket)
    except Exception:  # noqa: BLE001 - must never break request handling
        _LOGGER.debug("login_detail: could not classify a rejected request")
        return _found(None)


def _classify(
    hass: HomeAssistant, request: Any, endpoint: str | None, websocket: bool
) -> dict[str, Any]:
    if endpoint and endpoint.startswith("/auth/"):
        # /auth/token and /auth/login_flow reject on what is in the body - a
        # refresh token, an authorization code, a client ID. The body is not
        # read: it is spent, and it holds secrets.
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
    if len(token) > MAX_TOKEN_LENGTH:
        return _found(REJECTED_UNKNOWN_OR_REVOKED_TOKEN)
    try:
        issuer = jwt.decode(token, options={"verify_signature": False}).get("iss")
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


def _found(
    reason: str | None, user: Any = None, refresh_token: Any = None
) -> dict[str, Any]:
    """The result, with the user and client of a proven token filled in."""
    result: dict[str, Any] = dict.fromkeys(ATTRIBUTION_FIELDS)
    result["reason"] = reason
    if user is None:
        return result
    result["user_id"] = getattr(user, "id", None)
    result["username"] = _username_of(user, refresh_token)
    if refresh_token is not None:
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
