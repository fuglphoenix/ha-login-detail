"""Set up login_detail from the UI.

The integration has no options. The flow is therefore a plain confirmation:
Settings -> Devices & services -> Add integration -> Login Detail.

`single_config_entry: true` in the manifest makes core reject attempts to
create a second entry. The check in `async_step_user` is belt and braces - it
also works if the manifest key is ever ignored.

`async_step_import` exists so that a legacy `login_detail:` line in
configuration.yaml becomes a config entry on the first startup after the
upgrade. The line can then be removed; YAML is no longer needed.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
import probatio

from .const import DOMAIN, TITLE


class LoginDetailConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for login_detail."""

    VERSION = 1

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
