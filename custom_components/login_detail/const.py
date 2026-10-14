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

# Number of events stored on disk and shown in the sensor attributes.
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

# The endpoint every login flow entry is reported on.
ENDPOINT_LOGIN_FLOW = "/auth/login_flow"

# Repeated rejections of the same IP address on the same endpoint within this
# window are folded into one history entry with a count, so a misconfigured
# client retrying every few seconds cannot push real login attempts out of the
# history.
REJECTED_FOLD_WINDOW = timedelta(minutes=10)
