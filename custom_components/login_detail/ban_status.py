"""Read-only view of core's IP ban state.

Core keeps the ban list and the failed attempt counter on the aiohttp
application (`homeassistant.components.http.ban`, private API):

  KEY_BAN_MANAGER            IpBanManager; `.ip_bans_lookup` maps
                             ip_address -> IpBan
  KEY_FAILED_LOGIN_ATTEMPTS  defaultdict ip_address -> attempts
  KEY_LOGIN_THRESHOLD        the configured login_attempts_threshold

All three exist only when `ip_ban_enabled` is true. Nothing here writes to
them, and every function returns "unknown" instead of raising - if core
renames a key, the ban fields disappear and everything else keeps working.

Verified against Home Assistant 2025.10.0, 2026.4.0, 2026.9.0, 2026.10.1,
2026.10.2 and core dev of 2026-10-19.
"""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Any

from homeassistant.components.http import ban as http_ban
from homeassistant.core import HomeAssistant


@dataclass(frozen=True, slots=True)
class BanState:
    """Ban state of one IP address at one moment."""

    banned: bool
    attempts: int | None
    threshold: int | None


def _parse(ip: Any) -> IPv4Address | IPv6Address | None:
    try:
        return ip_address(str(ip))
    except ValueError:
        return None


def _key(name: str) -> Any:
    return getattr(http_ban, name, None)


def _ban_lookup(app: Any) -> dict[Any, Any] | None:
    """Core's ban list, or None if IP banning is not active."""
    key = _key("KEY_BAN_MANAGER")
    if app is None or key is None:
        return None
    try:
        manager = app.get(key)
    except Exception:  # noqa: BLE001 - unknown is the answer, not an error
        return None
    lookup = getattr(manager, "ip_bans_lookup", None)
    return lookup if isinstance(lookup, dict) else None


def http_app(hass: HomeAssistant) -> Any:
    """The aiohttp application of the running http server, if any."""
    return getattr(getattr(hass, "http", None), "app", None)


def ban_state(app: Any, ip: Any) -> BanState | None:
    """Ban state of `ip` on `app`, or None if it cannot be read."""
    lookup = _ban_lookup(app)
    address = _parse(ip)
    if lookup is None or address is None:
        return None
    attempts: int | None = None
    threshold: int | None = None
    try:
        counter = app.get(_key("KEY_FAILED_LOGIN_ATTEMPTS"))
        if isinstance(counter, dict):
            # .get, not [] - indexing a defaultdict would create the entry.
            value = counter.get(address)
            attempts = value if isinstance(value, int) else None
        value = app.get(_key("KEY_LOGIN_THRESHOLD"))
        threshold = value if isinstance(value, int) else None
    except Exception:  # noqa: BLE001 - the counters are optional detail
        attempts = threshold = None
    return BanState(address in lookup, attempts, threshold)


def banned_addresses(hass: HomeAssistant) -> set[IPv4Address | IPv6Address]:
    """Every banned address. Empty if IP banning is off or unreadable."""
    lookup = _ban_lookup(http_app(hass))
    return set(lookup) if lookup else set()


def is_banned(banned: set[IPv4Address | IPv6Address], ip: Any) -> bool:
    """Whether `ip` (as stored in the history) is in `banned`."""
    address = _parse(ip)
    return address is not None and address in banned
