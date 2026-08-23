## [1.1.0] - 2026-08-22

### Added
- Plane-of-array irradiance for configurable **surfaces**, each a config subentry with its own device. Entirely opt-in: no surfaces exist until one is added, so an entry that does not want them gains no entities. Surfaces are computed locally and add no API requests, and adding or editing one takes effect without a restart.
- Per surface: direct beam, sky diffuse, ground reflected and total irradiance sensors, plus angle of incidence. The components are published separately so shading masks can be applied to the beam alone and so beam and diffuse light can be weighted differently.
- A twelve-hour `forecast` attribute on each surface's direct beam sensor, at 15-minute resolution where the API supplies it, for anticipatory control.
- Sky primitives on the service device: global, direct and diffuse horizontal irradiance, solar azimuth and solar elevation. Disabled by default — the irradiances repeat the weather entity's attributes and the angles repeat `sun.sun`.
- `minutely_15` instantaneous irradiance is now requested alongside the hourly series. Instantaneous values multiplied by an instantaneous cosine are exact, whereas interval means are not at grazing incidence.
- `custom_templates/open_meteo_solar.jinja`, a template macro applying the same projection to planes that are not configured as surfaces.
- Tests for the solar geometry, the raw-block mechanism and the template macro, runnable without Home Assistant.

### Changed
- cos(zenith) is taken from the API's `terrestrial_radiation` rather than from locally computed astronomy. On a vertical surface the beam term carries a factor of cot(elevation), so a half-degree error becomes a 25% error in the beam near sunset. The scale factor relating terrestrial radiation to cos(zenith) is undocumented and differs by 3.4% over a year depending on convention, so it is fitted per update from high-sun samples instead of assumed, and rejected if implausible.
- Sample timestamps are treated as labelling the start of an interval whose value applies to the midpoint, which is what the API was measured to do.
- The per-field workaround for solar fields the bundled `open-meteo` library cannot deserialise is replaced by a general raw-block mechanism, which also carries the `minutely_15` block the library does not model at all.

### Requires
- Home Assistant 2025.3.0 or later, for config subentries.

## [1.0.4] - 2026-06-30

### Fixed
- Crash on data refresh when the API returns `windgusts_10m` as a list of null values (e.g. when wind gust data is unavailable for a location). mashumaro cannot deserialise `list[None]` as `Optional[list[float]]`, causing every update to fail. The field is now silently dropped before parsing and treated as unavailable.

## [1.0.3] - ...
