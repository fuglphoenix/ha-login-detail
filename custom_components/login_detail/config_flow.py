"""Set up login_detail from the UI.

Setting up is a plain confirmation: Settings -> Devices & services -> Add
integration -> Login Detail. The options (the cog on the entry) set how long
the sign-in log is kept - by number of entries and by age - whether the
admin-only "Sign-in log" panel is shown in the sidebar, and whether the
username of a failed login is kept when no user has it (off by default, see
`store_unknown_usernames` in const.py). Saving the options reloads the entry.

`single_config_entry: true` in the manifest makes core reject attempts to
create a second entry. The check in `async_step_user` is belt and braces - it
also works if the manifest key is ever ignored.

`async_step_import` exists so that a legacy `login_detail:` line in
configuration.yaml becomes a config entry on the first startup after the
upgrade. The line can then be removed; YAML is no longer needed.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
import probatio

from .const import (
    CONF_MAX_DAYS,
    CONF_MAX_ENTRIES,
    CONF_SHOW_PANEL,
    CONF_STORE_UNKNOWN_USERNAMES,
    DEFAULT_MAX_DAYS,
    DEFAULT_MAX_ENTRIES,
    DEFAULT_SHOW_PANEL,
    DEFAULT_STORE_UNKNOWN_USERNAMES,
    DOMAIN,
    MAX_DAYS,
    MAX_ENTRIES,
    MIN_DAYS,
    MIN_ENTRIES,
    TITLE,
)


class LoginDetailConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for login_detail."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> LoginDetailOptionsFlow:
        """Retention and panel."""
        return LoginDetailOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Added from the UI."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title=TITLE, data={})
        return self.async_show_form(step_id="user", data_schema=probatio.Schema({}))

    async def async_step_import(
        self, import_data: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Import the legacy YAML setup into a config entry."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title=TITLE, data={})


class LoginDetailOptionsFlow(OptionsFlowWithReload):
    """Retention of the sign-in log, and the side panel."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One form with all options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = probatio.Schema(
            {
                probatio.Required(
                    CONF_MAX_ENTRIES,
                    default=options.get(CONF_MAX_ENTRIES, DEFAULT_MAX_ENTRIES),
                ): probatio.All(
                    probatio.Coerce(int),
                    probatio.Range(min=MIN_ENTRIES, max=MAX_ENTRIES),
                ),
                probatio.Required(
                    CONF_MAX_DAYS,
                    default=options.get(CONF_MAX_DAYS, DEFAULT_MAX_DAYS),
                ): probatio.All(
                    probatio.Coerce(int), probatio.Range(min=MIN_DAYS, max=MAX_DAYS)
                ),
                probatio.Required(
                    CONF_SHOW_PANEL,
                    default=options.get(CONF_SHOW_PANEL, DEFAULT_SHOW_PANEL),
                ): bool,
                probatio.Required(
                    CONF_STORE_UNKNOWN_USERNAMES,
                    default=options.get(
                        CONF_STORE_UNKNOWN_USERNAMES, DEFAULT_STORE_UNKNOWN_USERNAMES
                    ),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
