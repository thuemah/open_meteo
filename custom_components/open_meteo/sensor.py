"""Plane-of-array irradiance sensors for Open-Meteo.

One device per configured surface, and the sky's own geometry on the service
device. The three irradiance components are published separately and never
only as a sum: terrain and overhangs block the beam while leaving most of the
sky term intact, and beam and diffuse light differ enough in luminous efficacy
that a consumer modelling indoor light needs them apart.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import CONF_NAME, DEGREE, UnitOfIrradiance
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ALBEDO,
    CONF_AZIMUTH,
    CONF_TILT,
    DOMAIN,
    FORECAST_HORIZON,
    SUBENTRY_TYPE_SURFACE,
)
from .coordinator import OpenMeteoConfigEntry, OpenMeteoDataUpdateCoordinator
from .series import SolarSample
from .solar import DEFAULT_ALBEDO, PoaComponents, transpose


@dataclass(frozen=True, kw_only=True)
class PoaSensorEntityDescription(SensorEntityDescription):
    """Describes a plane-of-array sensor."""

    value_fn: Callable[[PoaComponents], float | None]


POA_SENSORS: tuple[PoaSensorEntityDescription, ...] = (
    PoaSensorEntityDescription(
        key="poa_beam",
        translation_key="poa_beam",
        device_class=SensorDeviceClass.IRRADIANCE,
        native_unit_of_measurement=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda poa: poa.beam,
    ),
    PoaSensorEntityDescription(
        key="poa_sky",
        translation_key="poa_sky",
        device_class=SensorDeviceClass.IRRADIANCE,
        native_unit_of_measurement=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda poa: poa.sky,
    ),
    PoaSensorEntityDescription(
        key="poa_ground",
        translation_key="poa_ground",
        device_class=SensorDeviceClass.IRRADIANCE,
        native_unit_of_measurement=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda poa: poa.ground,
    ),
    PoaSensorEntityDescription(
        key="poa_total",
        translation_key="poa_total",
        device_class=SensorDeviceClass.IRRADIANCE,
        native_unit_of_measurement=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda poa: poa.total,
    ),
    PoaSensorEntityDescription(
        key="angle_of_incidence",
        translation_key="angle_of_incidence",
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda poa: poa.aoi,
    ),
)


@dataclass(frozen=True, kw_only=True)
class SunSensorEntityDescription(SensorEntityDescription):
    """Describes a solar geometry sensor."""

    value_fn: Callable[[SolarSample], float | None]


def _irradiance(
    key: str, value_fn: Callable[[SolarSample], float | None]
) -> SunSensorEntityDescription:
    """Return a description for one horizontal irradiance primitive."""
    return SunSensorEntityDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.IRRADIANCE,
        native_unit_of_measurement=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=value_fn,
    )


# The sky's own primitives, on the service device. Published as entities and
# not only as weather-entity attributes so a template can build a plane the
# config flow does not know about — see custom_templates/open_meteo_solar.jinja.
#
# Disabled by default. The irradiances repeat the weather entity's attributes
# and the two angles repeat `sun.sun`, so switching them on for everyone would
# be five entities and their recorder traffic that nobody asked for. Enable
# them if you want to build a plane in a template.
SUN_SENSORS: tuple[SunSensorEntityDescription, ...] = (
    _irradiance("global_horizontal_irradiance", lambda sample: sample.ghi),
    _irradiance(
        "direct_horizontal_irradiance", lambda sample: sample.direct_horizontal
    ),
    _irradiance(
        "diffuse_horizontal_irradiance",
        lambda sample: max(0.0, sample.ghi - sample.direct_horizontal),
    ),
    SunSensorEntityDescription(
        key="solar_azimuth",
        translation_key="solar_azimuth",
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda sample: sample.position.azimuth,
    ),
    SunSensorEntityDescription(
        key="solar_elevation",
        translation_key="solar_elevation",
        native_unit_of_measurement=DEGREE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=lambda sample: sample.position.apparent_elevation,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenMeteoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Open-Meteo sensors.

    Surfaces are opt-in: an entry with none configured gets no plane-of-array
    devices, and the sky primitives it does get are disabled until asked for.
    A weather integration should not grow two dozen entities on its own.
    """
    coordinator = entry.runtime_data

    async_add_entities(
        OpenMeteoSunSensor(
            entry=entry, coordinator=coordinator, description=description
        )
        for description in SUN_SENSORS
    )

    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_SURFACE:
            continue
        async_add_entities(
            (
                OpenMeteoPoaSensor(
                    coordinator=coordinator,
                    subentry=subentry,
                    description=description,
                )
                for description in POA_SENSORS
            ),
            config_subentry_id=subentry_id,
        )


class OpenMeteoSolarEntity(
    CoordinatorEntity[OpenMeteoDataUpdateCoordinator], SensorEntity
):
    """Shared plumbing for entities driven by the solar sample series."""

    _attr_has_entity_name = True

    @property
    def _sample(self) -> SolarSample | None:
        """Return the sample describing the present moment."""
        return self.coordinator.solar.sample_at(dt_util.utcnow())

    @property
    def available(self) -> bool:
        """Return whether the series currently covers now."""
        return super().available and self._sample is not None


class OpenMeteoSunSensor(OpenMeteoSolarEntity):
    """Where the sun is — the primitive every surface is derived from."""

    entity_description: SunSensorEntityDescription

    def __init__(
        self,
        *,
        entry: OpenMeteoConfigEntry,
        coordinator: OpenMeteoDataUpdateCoordinator,
        description: SunSensorEntityDescription,
    ) -> None:
        """Initialize a solar geometry sensor."""
        super().__init__(coordinator=coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="Open-Meteo",
            name=entry.title,
        )

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        if (sample := self._sample) is None:
            return None
        return self.entity_description.value_fn(sample)


class OpenMeteoPoaSensor(OpenMeteoSolarEntity):
    """One component of the irradiance on one configured surface."""

    entity_description: PoaSensorEntityDescription

    def __init__(
        self,
        *,
        coordinator: OpenMeteoDataUpdateCoordinator,
        subentry: ConfigSubentry,
        description: PoaSensorEntityDescription,
    ) -> None:
        """Initialize a plane-of-array sensor."""
        super().__init__(coordinator=coordinator)
        self.entity_description = description
        self._subentry = subentry
        self._attr_unique_id = f"{subentry.subentry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, subentry.subentry_id)},
            manufacturer="Open-Meteo",
            name=subentry.data.get(CONF_NAME, subentry.title),
        )

    @property
    def _tilt(self) -> float:
        return float(self._subentry.data[CONF_TILT])

    @property
    def _azimuth(self) -> float:
        return float(self._subentry.data[CONF_AZIMUTH])

    @property
    def _albedo(self) -> float:
        return float(self._subentry.data.get(CONF_ALBEDO, DEFAULT_ALBEDO))

    def _transpose(self, sample: SolarSample) -> PoaComponents:
        """Project one sample onto this surface."""
        return transpose(
            ghi=sample.ghi,
            direct_horizontal=sample.direct_horizontal,
            cos_zenith=sample.cos_zenith,
            solar_azimuth=sample.position.azimuth,
            apparent_elevation=sample.position.apparent_elevation,
            tilt=self._tilt,
            surface_azimuth=self._azimuth,
            albedo=self._albedo,
        )

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        if (sample := self._sample) is None:
            return None
        return self.entity_description.value_fn(self._transpose(sample))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the surface's geometry, and a beam forecast where it helps.

        The forecast rides on the beam sensor alone. Beam is what a screen or
        a blind is actually gated on, and repeating a multi-hour series on all
        five entities would be four copies of the same recorder traffic.
        """
        attributes: dict[str, Any] = {
            CONF_TILT: self._tilt,
            CONF_AZIMUTH: self._azimuth,
        }
        if self.entity_description.key != "poa_beam":
            return attributes

        attributes[CONF_ALBEDO] = self._albedo
        if (sample := self._sample) is not None:
            attributes["cos_zenith_from_api"] = sample.cos_zenith_from_api
            attributes["resolution_minutes"] = sample.interval_seconds // 60

        attributes["forecast"] = [
            {
                "datetime": upcoming.instant.isoformat(),
                "beam": round(self._transpose(upcoming).beam, 1),
            }
            for upcoming in self.coordinator.solar.upcoming(
                dt_util.utcnow(), FORECAST_HORIZON
            )
        ]
        return attributes
