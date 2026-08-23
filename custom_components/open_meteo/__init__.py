"""Support for Open-Meteo."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import OpenMeteoConfigEntry, OpenMeteoDataUpdateCoordinator

PLATFORMS = [Platform.SENSOR, Platform.WEATHER]


async def async_migrate_entry(hass: HomeAssistant, entry: OpenMeteoConfigEntry) -> bool:
    """Carry version 1 entries forward.

    Nothing in the entry data differs between the two versions: version 2 was
    reached by a build that has since been withdrawn, and reclaiming the
    number is not possible because entries created under it already exist.
    Bumping is all this has to do, and refusing to migrate would strand the
    entries the withdrawn build created.

    Surfaces that build created are deliberately left alone. They are ordinary
    configuration once they exist, and deleting someone's devices to undo a
    default they never chose is worse than leaving them to decide.
    """
    if entry.version == 1:
        hass.config_entries.async_update_entry(entry, version=2)
    return True


async def _async_reload_entry(
    hass: HomeAssistant, entry: OpenMeteoConfigEntry
) -> None:
    """Reload so an added or edited surface takes effect immediately.

    Surfaces arrive as subentries after setup has already run, and their
    entities are created during platform setup. Without this, adding a surface
    would appear to do nothing until the next restart.
    """
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: OpenMeteoConfigEntry) -> bool:
    """Set up Open-Meteo from a config entry."""
    coordinator = OpenMeteoDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: OpenMeteoConfigEntry) -> bool:
    """Unload Open-Meteo config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
