"""DataUpdateCoordinator for the Open-Meteo integration."""

from __future__ import annotations

import json
from types import SimpleNamespace

from open_meteo import (
    DailyParameters,
    Forecast,
    HourlyParameters,
    OpenMeteo,
    OpenMeteoError,
    PrecipitationUnit,
    TemperatureUnit,
    TimeFormat,
    WindSpeedUnit,
)
from yarl import URL

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_ZONE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, LOGGER, SCAN_INTERVAL
from .series import RawExtras, SolarData, build_solar_data, extract_block
from .solar import SOLAR_CONSTANT

# Solar fields requested as raw strings. The bundled open-meteo library version
# does not model any of them, and HA does not allow bumping the dependency, so
# they travel through the raw-extras mechanism below rather than through
# Forecast.from_dict().
#
# The `_instant` suffix is not decoration: an instantaneous irradiance
# multiplied by an instantaneous cosine is exact, whereas an interval mean
# multiplied by a mid-interval cosine is not, and the error peaks at grazing
# incidence — which is precisely the west-facing evening case this is for.
HOURLY_SOLAR_FIELDS: tuple[str, ...] = (
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "shortwave_radiation_instant",
    "direct_radiation_instant",
    "terrestrial_radiation_instant",
)

# `diffuse_radiation_instant` does not exist at 15-minute resolution, so
# diffuse is reconstructed as GHI - direct. `terrestrial_radiation_instant`
# does exist, and is what lets cos(zenith) come from the API rather than from
# our own astronomy in the range where ours is least trustworthy.
MINUTELY_15_SOLAR_FIELDS: tuple[str, ...] = (
    "shortwave_radiation_instant",
    "direct_radiation_instant",
    "terrestrial_radiation_instant",
)

type OpenMeteoConfigEntry = ConfigEntry[OpenMeteoDataUpdateCoordinator]


class OpenMeteoWithCurrent(OpenMeteo):
    """Subclass of OpenMeteo to support current and minutely_15 parameters."""

    raw_extras: RawExtras
    """Blocks from the most recent call that the library cannot deserialise."""

    async def forecast(
        self,
        *,
        latitude: float,
        longitude: float,
        timezone: str = "UTC",
        current_weather: bool = False,
        current: list[str] | None = None,
        daily: list[DailyParameters] | None = None,
        hourly: list[HourlyParameters | str] | None = None,
        minutely_15: list[str] | None = None,
        past_days: int = 0,
        precipitation_unit: PrecipitationUnit = PrecipitationUnit.MILLIMETERS,
        temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS,
        timeformat: TimeFormat = TimeFormat.ISO_8601,
        wind_speed_unit: WindSpeedUnit = WindSpeedUnit.KILOMETERS_PER_HOUR,
    ) -> Forecast:
        """Get weather forecast, keeping fields the library cannot model."""
        url = URL("https://api.open-meteo.com/v1/forecast").with_query(
            current_weather="true" if current_weather else "false",
            current=",".join(current) if current is not None else [],
            daily=",".join(daily) if daily is not None else [],
            hourly=",".join(hourly) if hourly is not None else [],
            minutely_15=",".join(minutely_15) if minutely_15 is not None else [],
            latitude=latitude,
            longitude=longitude,
            past_days=past_days,
            precipitation_unit=precipitation_unit,
            temperature_unit=temperature_unit,
            timeformat=timeformat,
            timezone=timezone,
            windspeed_unit=wind_speed_unit,
        )
        data_dict = json.loads(await self._request(url=url))
        utc_offset_seconds = int(data_dict.get("utc_offset_seconds", 0))

        extras = RawExtras()
        for name, fields, interval in (
            ("hourly", HOURLY_SOLAR_FIELDS, 3600),
            ("minutely_15", None, 900),
        ):
            if (
                block := extract_block(
                    data_dict, name, fields, utc_offset_seconds, interval
                )
            ) is not None:
                extras.blocks[name] = block

        if isinstance(data_dict.get("hourly"), dict):
            # mashumaro deserialises wind_gusts_10m as Optional[list[float]].
            # The API may return a list of None values when data is unavailable;
            # that fails validation, so drop the field and let it default to None.
            gusts = data_dict["hourly"].get("windgusts_10m")
            if isinstance(gusts, list) and any(value is None for value in gusts):
                data_dict["hourly"].pop("windgusts_10m")

        # Held on the client rather than bolted onto Forecast: the library's
        # dataclass is not ours to grow attributes on, and one coordinator owns
        # one client whose updates are serialised, so this is unambiguous.
        self.raw_extras = extras
        forecast = Forecast.from_dict(data_dict)

        if "current" in data_dict:
            # Normalize API keys to match the Python attribute names used by
            # the open-meteo library (e.g. "relativehumidity_2m" -> "relative_humidity_2m",
            # "windspeed_10m" -> "wind_speed_10m", "winddirection_10m" -> "wind_direction_10m",
            # "windgusts_10m" -> "wind_gusts_10m", "weathercode" -> "weather_code").
            normalized = {}
            for key, value in data_dict["current"].items():
                normalized_key = key
                normalized_key = normalized_key.replace(
                    "relativehumidity_", "relative_humidity_"
                )
                normalized_key = normalized_key.replace("windspeed_", "wind_speed_")
                normalized_key = normalized_key.replace(
                    "winddirection_", "wind_direction_"
                )
                normalized_key = normalized_key.replace("windgusts_", "wind_gusts_")
                normalized_key = normalized_key.replace("cloudcover", "cloud_cover")
                if normalized_key == "weathercode":
                    normalized_key = "weather_code"
                normalized[normalized_key] = value
            forecast.current = SimpleNamespace(**normalized)

        return forecast


class OpenMeteoDataUpdateCoordinator(DataUpdateCoordinator[Forecast]):
    """A Open-Meteo Data Update Coordinator."""

    config_entry: OpenMeteoConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: OpenMeteoConfigEntry) -> None:
        """Initialize the Open-Meteo coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{config_entry.data[CONF_ZONE]}",
            update_interval=SCAN_INTERVAL,
        )
        session = async_get_clientsession(hass)
        # Use our patched client
        self.open_meteo = OpenMeteoWithCurrent(session=session)
        self.open_meteo.raw_extras = RawExtras()
        self.solar = SolarData()

    @property
    def raw_extras(self) -> RawExtras:
        """Return blocks from the last response the library could not model."""
        return self.open_meteo.raw_extras

    async def _async_update_data(self) -> Forecast:
        """Fetch data from Open-Meteo."""
        if (zone := self.hass.states.get(self.config_entry.data[CONF_ZONE])) is None:
            raise UpdateFailed(f"Zone '{self.config_entry.data[CONF_ZONE]}' not found")

        latitude = zone.attributes[ATTR_LATITUDE]
        longitude = zone.attributes[ATTR_LONGITUDE]

        # Solar irradiance is included in `current` (the API returns its own
        # `time` and `interval` so consumers can tell whether the value is a
        # 15-min or 1-hour preceding mean) and in `hourly` for forecast use.
        current: list[HourlyParameters | str] = [
            HourlyParameters.TEMPERATURE_2M,
            HourlyParameters.WIND_SPEED_10M,
            HourlyParameters.WIND_DIRECTION_10M,
            HourlyParameters.WEATHER_CODE,
            HourlyParameters.CLOUD_COVER,
            HourlyParameters.WIND_GUSTS_10M,
            HourlyParameters.RELATIVE_HUMIDITY_2M,
            HourlyParameters.PRESSURE_MSL,
            "shortwave_radiation",
            "direct_normal_irradiance",
            "diffuse_radiation",
        ]

        hourly_fields: list[HourlyParameters | str] = [
            HourlyParameters.PRECIPITATION,
            HourlyParameters.TEMPERATURE_2M,
            HourlyParameters.WEATHER_CODE,
            HourlyParameters.WIND_DIRECTION_10M,
            HourlyParameters.WIND_SPEED_10M,
            HourlyParameters.RELATIVE_HUMIDITY_2M,
            HourlyParameters.CLOUD_COVER,
            HourlyParameters.PRESSURE_MSL,
            HourlyParameters.WIND_GUSTS_10M,
            *HOURLY_SOLAR_FIELDS,
        ]

        try:
            forecast = await self.open_meteo.forecast(
                latitude=latitude,
                longitude=longitude,
                current=current,
                daily=[
                    DailyParameters.PRECIPITATION_SUM,
                    DailyParameters.TEMPERATURE_2M_MAX,
                    DailyParameters.TEMPERATURE_2M_MIN,
                    DailyParameters.WEATHER_CODE,
                    DailyParameters.WIND_DIRECTION_10M_DOMINANT,
                    DailyParameters.WIND_SPEED_10M_MAX,
                ],
                hourly=hourly_fields,
                minutely_15=list(MINUTELY_15_SOLAR_FIELDS),
                precipitation_unit=PrecipitationUnit.MILLIMETERS,
                temperature_unit=TemperatureUnit.CELSIUS,
                timezone="auto",
                wind_speed_unit=WindSpeedUnit.KILOMETERS_PER_HOUR,
            )
        except OpenMeteoError as err:
            raise UpdateFailed("Open-Meteo API communication error") from err

        self.solar = build_solar_data(self.open_meteo.raw_extras, latitude, longitude)
        if self.solar.e0 is not None:
            LOGGER.debug(
                "Fitted extraterrestrial irradiance %.1f W/m2 (nominal %.0f) over %d samples",
                self.solar.e0,
                SOLAR_CONSTANT,
                len(self.solar.samples),
            )
        return forecast
