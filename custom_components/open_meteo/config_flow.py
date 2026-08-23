"""Config flow to configure the Open-Meteo integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.zone import DOMAIN as ZONE_DOMAIN
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME, CONF_ZONE
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import CONF_ALBEDO, CONF_AZIMUTH, CONF_TILT, DOMAIN, SUBENTRY_TYPE_SURFACE
from .solar import DEFAULT_ALBEDO


def surface_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return the schema describing one surface.

    Azimuth is compass degrees — 0 north, 90 east, 180 south, 270 west — to
    match `sun.sun`, so a surface can be checked against an entity the user
    already has. Tilt is measured from horizontal: 0 is a flat roof, 90 a
    wall.
    """
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            vol.Required(
                CONF_TILT, default=defaults.get(CONF_TILT, 90.0)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=90,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="°",
                )
            ),
            vol.Required(
                CONF_AZIMUTH, default=defaults.get(CONF_AZIMUTH, 180.0)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=360,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="°",
                )
            ),
            vol.Required(
                CONF_ALBEDO, default=defaults.get(CONF_ALBEDO, DEFAULT_ALBEDO)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=1, step=0.05, mode=NumberSelectorMode.BOX
                )
            ),
        }
    )


class OpenMeteoFlowHandler(ConfigFlow, domain=DOMAIN):
    """Config flow for OpenMeteo."""

    # Stays at 2 even though nothing in the entry data needs it. An earlier
    # build shipped VERSION = 2, so entries in the wild already carry it, and
    # Home Assistant refuses to load an entry newer than the code that reads
    # it. A version number, once written to someone's storage, is spent.
    VERSION = 2

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry types this integration supports."""
        return {SUBENTRY_TYPE_SURFACE: SurfaceSubentryFlowHandler}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_ZONE])
            self._abort_if_unique_id_configured()

            state = self.hass.states.get(user_input[CONF_ZONE])
            return self.async_create_entry(
                title=state.name if state else "Open-Meteo",
                data={CONF_ZONE: user_input[CONF_ZONE]},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ZONE): EntitySelector(
                        EntitySelectorConfig(domain=ZONE_DOMAIN),
                    ),
                }
            ),
        )


class SurfaceSubentryFlowHandler(ConfigSubentryFlow):
    """Add or reconfigure one surface.

    A surface is configuration, not weather. Keeping it here rather than as an
    attribute on the weather entity is what lets the entity stay a description
    of the sky while the building lives in the config layer — and it is also
    what keeps the feature opt-in, since an entry with no surfaces publishes
    no plane-of-array entities at all.
    """

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a surface."""
        if user_input is not None:
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(step_id="user", data_schema=surface_schema())

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure an existing surface."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                title=user_input[CONF_NAME],
                data=user_input,
            )
        return self.async_show_form(
            step_id="reconfigure", data_schema=surface_schema(dict(subentry.data))
        )
