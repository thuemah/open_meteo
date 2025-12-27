"""DataUpdateCoordinator for the Open-Meteo integration."""

from __future__ import annotations

import json
import logging
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

type OpenMeteoConfigEntry = ConfigEntry[OpenMeteoDataUpdateCoordinator]


class OpenMeteoWithCurrent(OpenMeteo):
    """Subclass of OpenMeteo to support current parameter."""

    async def forecast(
        self,
        *,
        latitude: float,
        longitude: float,
        timezone: str = "UTC",
        current_weather: bool = False,
        current: list[str] | None = None,
        daily: list[DailyParameters] | None = None,
        hourly: list[HourlyParameters] | None = None,
        past_days: int = 0,
        precipitation_unit: PrecipitationUnit = PrecipitationUnit.MILLIMETERS,
        temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS,
        timeformat: TimeFormat = TimeFormat.ISO_8601,
        wind_speed_unit: WindSpeedUnit = WindSpeedUnit.KILOMETERS_PER_HOUR,
    ) -> Forecast:
        """Get weather forecast with support for current parameter."""
        url = URL("https://api.open-meteo.com/v1/forecast").with_query(
            current_weather="true" if current_weather else "false",
            current=",".join(current) if current is not None else [],
            daily=",".join(daily) if daily is not None else [],
            hourly=",".join(hourly) if hourly is not None else [],
            latitude=latitude,
            longitude=longitude,
            past_days=past_days,
            precipitation_unit=precipitation_unit,
            temperature_unit=temperature_unit,
            timeformat=timeformat,
            timezone=timezone,
            windspeed_unit=wind_speed_unit,
        )
        data = await self._request(url=url)
        data_dict = json.loads(data)
        forecast = Forecast.from_dict(data_dict)

        if "current" in data_dict:
            # Attach the current data as a SimpleNamespace to allow dot access
            forecast.current = SimpleNamespace(**data_dict["current"])

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

    async def _async_update_data(self) -> Forecast:
        """Fetch data from Open-Meteo."""
        if (zone := self.hass.states.get(self.config_entry.data[CONF_ZONE])) is None:
            raise UpdateFailed(f"Zone '{self.config_entry.data[CONF_ZONE]}' not found")

        current = [
            HourlyParameters.TEMPERATURE_2M,
            HourlyParameters.WIND_SPEED_10M,
            HourlyParameters.WIND_DIRECTION_10M,
            HourlyParameters.WEATHER_CODE,
            HourlyParameters.CLOUD_COVER,
            HourlyParameters.WIND_GUSTS_10M,
        ]

        try:
            return await self.open_meteo.forecast(
                latitude=zone.attributes[ATTR_LATITUDE],
                longitude=zone.attributes[ATTR_LONGITUDE],
                current=current,
                daily=[
                    DailyParameters.PRECIPITATION_SUM,
                    DailyParameters.TEMPERATURE_2M_MAX,
                    DailyParameters.TEMPERATURE_2M_MIN,
                    DailyParameters.WEATHER_CODE,
                    DailyParameters.WIND_DIRECTION_10M_DOMINANT,
                    DailyParameters.WIND_SPEED_10M_MAX,
                ],
                hourly=[
                    HourlyParameters.PRECIPITATION,
                    HourlyParameters.TEMPERATURE_2M,
                    HourlyParameters.WEATHER_CODE,
                    HourlyParameters.WIND_DIRECTION_10M,
                    HourlyParameters.WIND_SPEED_10M,
                    HourlyParameters.RELATIVE_HUMIDITY_2M,
                    HourlyParameters.CLOUD_COVER,
                    HourlyParameters.PRESSURE_MSL,
                    HourlyParameters.WIND_GUSTS_10M,
                ],
                precipitation_unit=PrecipitationUnit.MILLIMETERS,
                temperature_unit=TemperatureUnit.CELSIUS,
                timezone="auto",
                wind_speed_unit=WindSpeedUnit.KILOMETERS_PER_HOUR,
                #forecast_days=14,
            )
        except OpenMeteoError as err:
            raise UpdateFailed("Open-Meteo API communication error") from err
