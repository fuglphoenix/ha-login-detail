"""Shared constants for login_detail."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "login_detail"

# Title of the config entry in Settings -> Devices & services.
TITLE = "Login Detail"

EVENT_LOGIN_FAILED = "login_detail_failed"
EVENT_LOGIN_SUCCEEDED = "login_detail_succeeded"

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
