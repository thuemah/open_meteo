# Open-Meteo Custom Component for Home Assistant

This is a custom component for Home Assistant that serves as a drop-in replacement for the built-in Open-Meteo integration.

## Purpose

The primary goal of this custom component is to provide a more comprehensive hourly forecast by including weather parameters that are missing from the standard Home Assistant integration.

The official Open-Meteo integration is great, but it strips out a lot of valuable data from the hourly forecast to fit the standard Home Assistant weather model.

If you are doing advanced energy management (like EMHASS), predictive heating analytics, or complex Node-RED automations, you need rich data. This custom component drops right in and supercharges your hourly forecast with:

💨 Wind Gusts: Essential for heat-loss calculations.

☁️ Cloud Coverage: Crucial for solar gain prediction and PV forecasting.

💧 Humidity & Pressure: For advanced environmental modeling.

☀️ Solar Irradiance: Shortwave (GHI), direct normal (DNI) and diffuse (DHI) radiation — both in the hourly forecast and as live entity attributes. This is what the standard weather model has no place for at all, and it is what makes proper solar gain and PV modelling possible.

📅 Extended Hourly Range: Up to 7 days of hourly data


## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Click the three dots in the top right corner
3. Select **Custom repositories**
4. Add `https://github.com/thuemah/open_meteo` as an **Integration**
5. Install the integration
6. Restart Home Assistant

### Manual

1. Copy the `custom_components/open_meteo` folder into your `custom_components` directory
2. Restart Home Assistant
3. Go to **Settings → Devices & Services**
4. Click **Add Integration** and search for **Open-Meteo**

## Features

This integration extends the `forecast_hourly` functionality by adding the following parameters:

*   **Humidity** (`relative_humidity_2m`)
*   **Cloud Coverage** (`cloud_cover`)
*   **Atmospheric Pressure** (`pressure_msl`)
*   **Wind Gusts** (`wind_gusts_10m`)
*   **Shortwave Radiation / GHI** (`shortwave_radiation`)
*   **Direct Normal Irradiance / DNI** (`direct_normal_irradiance`)
*   **Diffuse Radiation / DHI** (`diffuse_radiation`)

The hourly forecast reaches roughly 6–7 days ahead at full 1-hour resolution, so it stays usable well past the point where most providers drop to daily or coarser steps.

### Live solar attributes

The three irradiance values are also exposed as attributes on the weather entity for the current observation, alongside two fields that let you tell fresh data from stale:

*   `solar_data_time` — the API's own timestamp for the current observation
*   `solar_data_interval` — the aggregation window in seconds (`900` for a 15-minute preceding mean, `3600` for hourly)

Check `solar_data_time` before trusting the values for real-time decisions; the attributes are omitted entirely when the API returns no current block.

## Versioning

The version of this component is deliberately kept in the **1.x** range — see `custom_components/open_meteo/manifest.json` for the current value.

This is done to ensure Home Assistant prioritizes this custom component over the built-in integration, whose version tracks the Open-Meteo library it uses (currently 0.3.x). Any 1.x version outranks that, so the extended version is the one that loads.
