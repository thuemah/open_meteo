## [1.0.4] - 2026-06-30

### Fixed
- Crash on data refresh when the API returns `windgusts_10m` as a list of null values (e.g. when wind gust data is unavailable for a location). mashumaro cannot deserialise `list[None]` as `Optional[list[float]]`, causing every update to fail. The field is now silently dropped before parsing and treated as unavailable.

## [1.0.3] - ...
