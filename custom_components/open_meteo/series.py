"""Raw time-series blocks and the solar samples assembled from them.

Kept free of Home Assistant imports so the mechanism and the assembly can be
tested without a running hass — the same reason `solar.py` is.

The bundled open-meteo library models neither the solar fields nor the
`minutely_15` block at all, and the dependency cannot be bumped. Rather than a
per-field pop-and-reattach for each new field, blocks the library cannot
deserialise are lifted out wholesale here and travel alongside the parsed
`Forecast`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from .solar import (
    E0_FIT_MIN_ELEVATION,
    E0_FIT_MIN_SAMPLES,
    SolarPosition,
    fit_e0,
    sample_instant,
    solar_position,
)

LOGGER = logging.getLogger(__package__)

@dataclass(frozen=True, slots=True)
class SolarSample:
    """One instant of sky, with the geometry that goes with it.

    Irradiances are instantaneous values; `instant` is the moment they
    describe, which is the *midpoint* of the labelled interval rather than its
    timestamp.
    """

    instant: datetime
    interval_seconds: int
    position: SolarPosition
    cos_zenith: float
    """From the API where terrestrial radiation was available, else geometric."""
    cos_zenith_from_api: bool
    ghi: float
    direct_horizontal: float


@dataclass(slots=True)
class SolarData:
    """Everything the plane-of-array sensors need, precomputed once."""

    samples: list[SolarSample] = field(default_factory=list)
    e0: float | None = None
    """Fitted extraterrestrial constant, or None when the fit was rejected."""

    def sample_at(self, moment: datetime) -> SolarSample | None:
        """Return the sample whose interval contains `moment`.

        None rather than the nearest sample when nothing covers it: a value
        from hours ago published as the present is worse than an unavailable
        entity, and the series normally starts at local midnight and runs days
        ahead, so a miss means something is actually wrong.
        """
        for sample in self.samples:
            half = timedelta(seconds=sample.interval_seconds / 2)
            if sample.instant - half <= moment < sample.instant + half:
                return sample
        return None

    def upcoming(self, after: datetime, limit: timedelta) -> list[SolarSample]:
        """Return samples from `after` up to `limit` ahead of it."""
        return [s for s in self.samples if after <= s.instant <= after + limit]


@dataclass(slots=True)
class RawBlock:
    """A time series the bundled library cannot deserialise.

    One mechanism, replacing what used to be a per-field pop-and-reattach
    dance: `hourly` carries fields the library does not model, and
    `minutely_15` it does not model at all.
    """

    time: list[datetime]
    interval_seconds: int
    values: dict[str, list[float | None]]

    def value(self, field_name: str, index: int) -> float | None:
        """Return one value, or None when absent, short or null."""
        series = self.values.get(field_name)
        if series is None or index >= len(series):
            return None
        return series[index]


@dataclass(slots=True)
class RawExtras:
    """Raw blocks pulled aside before deserialisation, keyed by block name."""

    blocks: dict[str, RawBlock] = field(default_factory=dict)

    def block(self, name: str) -> RawBlock | None:
        """Return a block by API name, or None when it was not returned."""
        return self.blocks.get(name)


def _parse_times(raw: list[str], utc_offset_seconds: int) -> list[datetime]:
    """Parse the API's local-naive ISO timestamps into aware datetimes."""
    tzinfo = timezone(timedelta(seconds=utc_offset_seconds))
    parsed: list[datetime] = []
    for value in raw:
        moment = datetime.fromisoformat(value)
        parsed.append(moment if moment.tzinfo else moment.replace(tzinfo=tzinfo))
    return parsed


def extract_block(
    payload: dict[str, Any],
    name: str,
    fields: tuple[str, ...] | None,
    utc_offset_seconds: int,
    interval_seconds: int,
) -> RawBlock | None:
    """Pull a block (or named fields of one) out of the payload.

    Passing `fields=None` removes the whole block, for one the library has no
    concept of; passing names removes just those, leaving the rest to
    deserialise normally.
    """
    block = payload.get(name)
    if not isinstance(block, dict) or "time" not in block:
        return None

    if fields is None:
        payload.pop(name)
        values = {k: v for k, v in block.items() if k != "time" and isinstance(v, list)}
        times = block["time"]
    else:
        values = {}
        for key in fields:
            if isinstance(block.get(key), list):
                values[key] = block.pop(key)
        if not values:
            return None
        times = block["time"]

    if not isinstance(times, list):
        return None

    return RawBlock(
        time=_parse_times(times, utc_offset_seconds),
        interval_seconds=interval_seconds,
        values=values,
    )


def build_solar_data(
    extras: RawExtras, latitude: float, longitude: float
) -> SolarData:
    """Turn raw irradiance blocks into geometry-resolved samples.

    Quarter-hourly samples are preferred where the API supplies them and
    hourly ones fill in beyond that horizon.

    cos(zenith) is taken from the API's own `terrestrial_radiation`, which is
    E0 * cos(zenith), with E0 fitted from high-sun samples of the same series.
    Fitting it means the API's undocumented choice between a flat solar
    constant and an eccentricity-corrected one — a 3.4% swing over a year —
    never has to be guessed, and any constant offset between the API's solar
    position and ours is absorbed with it. The fit is confined to high sun
    because that is the only place our own astronomy is good enough to serve
    as the reference; the result is then used where it is not.
    """
    fine = extras.block("minutely_15")
    coarse = extras.block("hourly")
    if fine is None and coarse is None:
        return SolarData()

    # First pass: geometry only, so E0 can be fitted before it is needed.
    staged: list[tuple[RawBlock, int, datetime, SolarPosition]] = []
    fit_samples: list[tuple[float, float]] = []
    fine_covers_until: datetime | None = None

    for block in (fine, coarse):
        if block is None:
            continue
        for index, timestamp in enumerate(block.time):
            instant = sample_instant(timestamp, block.interval_seconds)
            if (
                block is coarse
                and fine_covers_until is not None
                and instant < fine_covers_until
            ):
                # Quarter-hourly samples already describe this span. Hourly
                # midpoints fall between them rather than on them, so this
                # window has to be excluded by span, not by equality.
                continue
            position = solar_position(instant, latitude, longitude)
            staged.append((block, index, instant, position))

            terrestrial = block.value("terrestrial_radiation_instant", index)
            if (
                terrestrial is not None
                and position.apparent_elevation >= E0_FIT_MIN_ELEVATION
            ):
                fit_samples.append((terrestrial, position.cos_zenith))
        if block is fine and block.time:
            fine_covers_until = sample_instant(
                block.time[-1], block.interval_seconds
            ) + timedelta(seconds=block.interval_seconds / 2)

    e0 = fit_e0(fit_samples)
    if e0 is None:
        if len(fit_samples) < E0_FIT_MIN_SAMPLES:
            # Normal at high latitude in winter, and on the first update of a
            # short forecast window. Not worth a warning.
            LOGGER.debug(
                "Only %d high-sun samples available to fit extraterrestrial "
                "irradiance; using locally computed solar geometry",
                len(fit_samples),
            )
        else:
            LOGGER.warning(
                "Fitted extraterrestrial irradiance from %d samples fell outside the "
                "credible band; terrestrial_radiation may have changed meaning. "
                "Falling back to locally computed solar geometry",
                len(fit_samples),
            )

    # Second pass: irradiances, with cos(zenith) from the API where possible.
    samples: list[SolarSample] = []
    for block, index, instant, position in staged:
        ghi = block.value("shortwave_radiation_instant", index)
        direct = block.value("direct_radiation_instant", index)
        if ghi is None or direct is None:
            continue

        terrestrial = block.value("terrestrial_radiation_instant", index)
        if e0 is not None and terrestrial is not None:
            cos_zenith = max(0.0, min(1.0, terrestrial / e0))
            from_api = True
        else:
            cos_zenith = position.cos_zenith
            from_api = False

        samples.append(
            SolarSample(
                instant=instant,
                interval_seconds=block.interval_seconds,
                position=position,
                cos_zenith=cos_zenith,
                cos_zenith_from_api=from_api,
                ghi=max(0.0, ghi),
                direct_horizontal=max(0.0, direct),
            )
        )

    samples.sort(key=lambda s: s.instant)
    return SolarData(samples=samples, e0=e0)
