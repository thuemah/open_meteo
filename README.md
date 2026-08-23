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

🧱 Plane-of-Array Irradiance: Sunlight on a wall, a window or a roof pitch — not just on the horizontal — with the direct, sky and ground components kept separate. Entirely opt-in: nothing appears until you add a surface.

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

## Surfaces and plane-of-array irradiance

Irradiance on the horizontal tells you little about a west-facing window at seven in the evening. The integration therefore projects the sky onto any number of **surfaces** — planes described by a tilt and a compass azimuth — and publishes each one as its own device.

### This is opt-in, and stays out of the way until you ask for it

Install the integration and nothing changes: no surfaces exist, no plane-of-array devices are created, and the entity count is what it was before. You get surfaces only by adding them, under **Settings → Devices & Services → Open-Meteo → Add surface**.

That is deliberate. Projecting light onto a building is not a standard expectation for a weather integration, and this component's whole purpose is to be a *drop-in replacement* — quietly adding two dozen devices and entities would contradict that. Each surface also carries a real cost even though it carries no API cost: five sensors, each writing to the recorder on every update, and a beam forecast attribute of a few kilobytes alongside them. Paying that for four faces you never asked about is not a sensible default.

The projection itself is free of network cost. It is computed locally from the same API response the weather entity already fetches, so a surface never adds a request, and a fifth surface costs no more than the first.

If you want the four cardinal faces, add them: tilt 90° with azimuth 180, 90, 270 and 0.

*   **Tilt** is measured from horizontal — 0° a flat roof, 90° a wall.
*   **Azimuth** is compass degrees — 0° north, 90° east, 180° south, 270° west. This matches `sun.sun`, so you can check a surface against an entity you already have. (Open-Meteo's own `global_tilted_irradiance` parameter uses a different convention, where 0 is south; nothing here uses it.)
*   **Ground reflectance** defaults to 0.2, which suits most ground. Raise it for snow.

### Why the components are separate

Each surface publishes four irradiance sensors rather than one:

| Sensor | What it is |
| --- | --- |
| Direct beam | Sunlight arriving straight from the sun's disc |
| Sky diffuse | Light scattered by the sky, from the half of the dome the surface can see |
| Ground reflected | Light bounced up off the ground in front of the surface |
| Total irradiance | The three added together |

Plus an **Angle of incidence** sensor, in degrees off the surface normal.

The split is the useful part. A neighbouring roof or a deep overhang blocks the *beam* while leaving most of the sky term intact, so a shading mask belongs on the beam sensor alone:

```yaml
template:
  - sensor:
      - name: West window beam, shaded
        unit_of_measurement: W/m²
        device_class: irradiance
        state: >
          {% set beam = states('sensor.west_facade_direct_beam') | float(0) %}
          {% set az = states('sensor.home_solar_azimuth') | float(0) %}
          {# The neighbour's roof clears the window once the sun is past 285° #}
          {{ beam if az > 285 else 0 }}
```

Beam and diffuse light also differ substantially in luminous efficacy, so anything converting W/m² to lux needs them apart rather than summed.

### Forecasting the beam

The **Direct beam** sensor carries a `forecast` attribute reaching twelve hours ahead, at 15-minute resolution for as far as the API supplies it and hourly beyond. That is what makes anticipatory control possible — lowering a screen before the facade is hit rather than a few minutes after:

```yaml
{% set f = state_attr('sensor.west_facade_direct_beam', 'forecast') %}
{{ f | selectattr('beam', '>', 250) | map(attribute='datetime') | first }}
```

The attribute is a list, and the recorder stores attributes with every state change. If you do not need its history:

```yaml
recorder:
  exclude:
    entities:
      - sensor.west_facade_direct_beam
```

### Sky primitives

The service device also publishes what every surface is derived from: global, direct and diffuse horizontal irradiance, plus solar azimuth and elevation. Those are properties of the sky at your location, not of your building.

**These five are disabled by default.** The irradiances repeat what is already on the weather entity as attributes, and the two angles repeat `sun.sun`, so enabling them for everyone would be five entities and their history that nobody asked for. Turn on the ones you need from the integration's entity list — the template example below needs four of them.

### Ad-hoc planes in templates

For a plane you have not configured as a surface, `custom_templates/open_meteo_solar.jinja` in this repository does the same projection inside a template sensor. Copy it to `<config>/custom_templates/`, enable the four sky sensors it reads, reload templates, and:

```yaml
template:
  - sensor:
      - name: Skylight irradiance
        unit_of_measurement: W/m²
        state: >
          {% from 'open_meteo_solar.jinja' import poa %}
          {% set p = poa(
               tilt = 20,
               surface_azimuth = 110,
               ghi = states('sensor.home_global_horizontal_irradiance') | float(0),
               direct_horizontal = states('sensor.home_direct_horizontal_irradiance') | float(0),
               sun_azimuth = states('sensor.home_solar_azimuth') | float(0),
               sun_elevation = states('sensor.home_solar_elevation') | float(-90),
             ) | from_json %}
          {{ p.total }}
```

A configured surface is more accurate below about five degrees of sun elevation — see the note in the macro — so anything you gate on at sunset is worth configuring properly.

## Accuracy and its limits

The sky model is isotropic with a fixed ground reflectance, the same simplification Open-Meteo applies to its own tilted-irradiance product. Two details are handled more carefully than that description suggests:

*   **Instantaneous values, not interval means.** Multiplying an interval mean by a mid-interval cosine is wrong at grazing incidence, which is exactly the west-facing evening case. The integration requests the `_instant` variants, where the multiplication is exact.
*   **cos(zenith) comes from the API.** On a vertical surface the beam term carries a factor of cot(elevation) — 19 at 3°, 57 at 1° — so a half-degree error in solar position becomes a 25% error in the beam. Rather than rely on locally computed astronomy there, the integration recovers cos(zenith) from the API's own terrestrial radiation, calibrating the scale factor against high-sun samples where local astronomy is reliable.

Below 0.5° of elevation the beam is reported as zero. That is a numerical floor, not a geometric one: the division by cos(zenith) magnifies quantisation to the point where the number stops meaning anything.

What this does **not** model, and what no amount of extra physics here will fix: window transmittance, glazing area, curtains, dirt, and how the light is distributed once inside a room. If you are approximating an indoor lux sensor, expect to fit a coefficient or two per room against a real measurement. Keeping the components separate is what makes those coefficients physically meaningful rather than one opaque fudge factor.

## Development

The geometry, the raw-block mechanism and the shipped template macro are tested without Home Assistant:

```bash
python3 -m unittest discover -s tests
```

## Versioning

The version of this component is deliberately kept in the **1.x** range — see `custom_components/open_meteo/manifest.json` for the current value.

This is done to ensure Home Assistant prioritizes this custom component over the built-in integration, whose version tracks the Open-Meteo library it uses (currently 0.3.x). Any 1.x version outranks that, so the extended version is the one that loads.
