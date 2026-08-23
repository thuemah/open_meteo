"""Diagnostics support for Open-Meteo."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant

from .coordinator import OpenMeteoConfigEntry

TO_REDACT = {
    CONF_LATITUDE,
    CONF_LONGITUDE,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: OpenMeteoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    solar = coordinator.solar
    from_api = sum(1 for sample in solar.samples if sample.cos_zenith_from_api)
    return {
        "forecast": async_redact_data(coordinator.data.to_dict(), TO_REDACT),
        "solar": {
            # The fitted value answers, empirically, a question the API does
            # not document: whether its terrestrial radiation carries an
            # eccentricity correction. A value pinned at 1361 all year says
            # it does not; one tracking 1316-1407 says it does.
            "fitted_extraterrestrial_irradiance": solar.e0,
            "samples": len(solar.samples),
            "samples_with_api_cos_zenith": from_api,
            "resolutions_minutes": sorted(
                {sample.interval_seconds // 60 for sample in solar.samples}
            ),
        },
    }
