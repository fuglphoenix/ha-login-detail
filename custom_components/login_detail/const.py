"""Shared constants for login_detail."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "login_detail"

# Title of the config entry in Settings -> Devices & services.
TITLE = "Login Detail"

EVENT_LOGIN_FAILED = "login_detail_failed"
EVENT_LOGIN_SUCCEEDED = "login_detail_succeeded"
EVENT_REQUEST_REJECTED = "login_detail_request_rejected"

SIGNAL_UPDATED = f"{DOMAIN}_updated"

# The smallest number of events stored on disk, and the number the sensors
# looked at before 0.7.0. The actual cap is the `max_entries` option.
HISTORY_LIMIT = 50
HISTORY_ATTRIBUTE_LIMIT = 25

# The window the 24-hour counters are computed over.
WINDOW = timedelta(hours=24)

# How often the counters are recomputed, so they drop on their own as the
# window slides, and active sessions are re-read. Logins are rare; anything
# faster only writes noise to the recorder.
UPDATE_INTERVAL = timedelta(minutes=5)

RESULT_OK = "ok"

# The password was correct, but the flow never got past 2FA. The entry is
# replaced by `ok` if the same flow completes.
RESULT_MFA_PENDING = "mfa_pending"

# A request outside the login flow was rejected for invalid authentication -
# typically an expired or wrong access token on /api/mcp, /api/websocket or
# /auth/token. There is no username to report.
RESULT_REQUEST_REJECTED = "invalid_request_auth"

# Why a request was rejected (0.7.1), stored as `reason` next to the result
# above. Only the four marked with * name a user, and only once the token's
# signature has been verified against the refresh token it claims to belong
# to - by core for a request it authenticated, by rejection.py otherwise.
REJECTED_NO_CREDENTIALS = "no_credentials"
REJECTED_EXPIRED_TOKEN = "expired_token"  # *
REJECTED_USER_NOT_ALLOWED = "user_not_allowed"  # *
REJECTED_UNKNOWN_OR_REVOKED_TOKEN = "unknown_or_revoked_token"
REJECTED_MALFORMED_CREDENTIALS = "malformed_credentials"
REJECTED_INVALID_SIGNED_PATH = "invalid_signed_path"
REJECTED_NOT_ADMIN = "not_admin"  # *
REJECTED_PERMISSION_DENIED = "permission_denied"  # *
REJECTED_INVALID_AUTH_REQUEST = "invalid_auth_request"

# The endpoint every login flow entry is reported on.
ENDPOINT_LOGIN_FLOW = "/auth/login_flow"

# Repeated rejections of the same IP address on the same endpoint within this
# window are folded into one history entry with a count, so a misconfigured
# client retrying every few seconds cannot push real login attempts out of the
# history.
REJECTED_FOLD_WINDOW = timedelta(minutes=10)

# ---- Sign-in log (0.7.0) ---------------------------------------------------

# The `type` of an entry in the sign-in log, as proposed for core in
# home-assistant/architecture#1346.
LOG_TYPE_LOGIN_FAILED = "auth.login_failed"
LOG_TYPE_LOGIN_SUCCESS = "auth.login_success"
LOG_TYPE_REQUEST_REJECTED = "auth.request_rejected"
LOG_TYPES = (LOG_TYPE_LOGIN_FAILED, LOG_TYPE_LOGIN_SUCCESS, LOG_TYPE_REQUEST_REJECTED)

# A user agent is free text set by the client. It is cut, not parsed.
USER_AGENT_MAX_LENGTH = 256

# Retention, configurable in the options. The log holds personal data
# (username, IP address, user agent), so it is bounded both by count and by
# age, whichever comes first.
CONF_MAX_ENTRIES = "max_entries"
CONF_MAX_DAYS = "max_days"
CONF_SHOW_PANEL = "show_panel"
DEFAULT_MAX_ENTRIES = 500
DEFAULT_MAX_DAYS = 7
DEFAULT_SHOW_PANEL = True
MIN_ENTRIES = HISTORY_LIMIT
MAX_ENTRIES = 5000
MIN_DAYS = 1
MAX_DAYS = 365

# How often entries older than the age limit are removed, so they disappear
# even when nothing new is recorded.
PRUNE_INTERVAL = timedelta(hours=1)

# The admin-only side panel.
PANEL_URL_PATH = "login-detail"
PANEL_COMPONENT = "login-detail-sign-in-log"
PANEL_TITLE = "Sign-in log"
PANEL_ICON = "mdi:shield-account-outline"
PANEL_STATIC_URL = f"/{DOMAIN}_static"
