# Open-Meteo Custom Component for Home Assistant

This is a custom component for Home Assistant that serves as a drop-in replacement for the built-in Open-Meteo integration.

## Purpose

The primary goal of this custom component is to provide a more comprehensive hourly forecast by including weather parameters that are missing from the standard Home Assistant integration.

## Features

This integration extends the `forecast_hourly` functionality by adding the following parameters:

*   **Humidity** (`relative_humidity_2m`)
*   **Cloud Coverage** (`cloud_cover`)
*   **Atmospheric Pressure** (`pressure_msl`)
*   **Wind Gusts** (`wind_gusts_10m`)

## Versioning

The version of this component is explicitly set to **1.0.0**.

This is done to ensure that Home Assistant prioritizes this custom component over the built-in integration, which typically has a version number corresponding to the Open-Meteo library version it uses (e.g., 0.3.x). By setting the version to 1.0.0, we force Home Assistant to load this extended version.
