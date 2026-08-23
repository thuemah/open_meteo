"""Solar geometry and plane-of-array transposition.

Deliberately free of Home Assistant imports: the numerics here are the part
worth testing in isolation, and they need no hass to be correct.

Two conventions are fixed here and must not drift:

*   **Azimuth is compass degrees** — 0 = north, 90 = east, 180 = south,
    270 = west. This matches `sun.sun`'s `azimuth` attribute, so a user can
    sanity-check a surface against the entity they already have. Note that
    Open-Meteo's own `global_tilted_irradiance` parameter uses a different
    convention (0 = south, negative = east); nothing here talks to it.
*   **A sample's timestamp labels the start of its interval, but the value
    applies to the interval midpoint.** Measured against the API, not assumed:
    over 71 samples spanning eleven days, evaluating solar position at the
    midpoint reproduced the API's implied zenith angle with a median error of
    0.00 deg and a spread of 0.04 deg, against roughly +/-0.9 deg with a
    spread of 1.9 deg for either endpoint. `sample_instant()` is the only
    place that offset is applied.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math

# Nominal solar constant, used only as a sanity bound and as a last-resort
# fallback. The value actually used is fitted from the API — see fit_e0().
SOLAR_CONSTANT: float = 1361.0

# Open-Meteo does not document whether its `terrestrial_radiation` applies an
# eccentricity correction; over a year that is a 3.4% swing. Rather than guess,
# E0 is fitted per update and only accepted inside this band. Outside it the
# series means something other than what we think, and we fall back.
E0_MIN: float = 1300.0
E0_MAX: float = 1420.0

# Above this elevation our own solar position is good to well under 0.05 deg
# and the 1/cos(zenith) amplification is negligible, so these samples are the
# only ones trusted to fit E0.
E0_FIT_MIN_ELEVATION: float = 20.0
E0_FIT_MIN_SAMPLES: int = 12

# Below this elevation the beam is set to zero. This is a *numerical* floor,
# not a geometric one: direct_radiation is quantised, and dividing it by
# cos(zenith) here multiplies that quantisation by more than a hundred.
# Terrain and neighbouring buildings are a separate, per-surface concern that
# belongs in the consumer's masking, not in this module.
BEAM_FLOOR_ELEVATION: float = 0.5

DEFAULT_ALBEDO: float = 0.2


@dataclass(frozen=True, slots=True)
class SolarPosition:
    """Where the sun is, geometrically and apparently."""

    elevation: float
    """Geometric elevation in degrees, no refraction applied."""

    apparent_elevation: float
    """Refracted elevation in degrees — where the sun appears to be."""

    azimuth: float
    """Compass azimuth in degrees: 0 = north, 90 = east."""

    @property
    def cos_zenith(self) -> float:
        """Cosine of the apparent zenith angle, clamped to [0, 1]."""
        return max(0.0, min(1.0, math.sin(math.radians(self.apparent_elevation))))


@dataclass(frozen=True, slots=True)
class PoaComponents:
    """Irradiance on a tilted plane, kept decomposed.

    The decomposition is the point. Terrain and overhangs block the beam while
    leaving most of the sky term intact, and beam and diffuse light have
    materially different luminous efficacies, so a consumer that only ever
    sees `total` cannot model either.
    """

    beam: float
    sky: float
    ground: float
    aoi: float | None
    """Angle of incidence in degrees, or None when the sun is behind the plane."""

    @property
    def total(self) -> float:
        """Sum of the three components."""
        return self.beam + self.sky + self.ground


def sample_instant(timestamp: datetime, interval_seconds: int) -> datetime:
    """Return the instant a sample's value actually describes.

    See the module docstring: the timestamp labels the interval start, the
    value belongs to its midpoint.
    """
    return timestamp + timedelta(seconds=interval_seconds / 2)


def solar_position(when: datetime, latitude: float, longitude: float) -> SolarPosition:
    """Compute solar position with the NOAA algorithm.

    `when` must be timezone-aware. Accurate to well under a tenth of a degree
    for our purposes, and — more to the point — deterministic and testable,
    which the alternative of trusting an undocumented API field is not.
    """
    if when.tzinfo is None:
        raise ValueError("solar_position() requires a timezone-aware datetime")

    julian_day = when.timestamp() / 86400.0 + 2440587.5
    t = (julian_day - 2451545.0) / 36525.0

    mean_long = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360
    mean_anom = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)

    anom = math.radians(mean_anom)
    centre = (
        math.sin(anom) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * anom) * (0.019993 - 0.000101 * t)
        + math.sin(3 * anom) * 0.000289
    )

    omega = 125.04 - 1934.136 * t
    apparent_long = mean_long + centre - 0.00569 - 0.00478 * math.sin(math.radians(omega))

    obliquity = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
    obliquity += 0.00256 * math.cos(math.radians(omega))

    declination = math.degrees(
        math.asin(math.sin(math.radians(obliquity)) * math.sin(math.radians(apparent_long)))
    )

    y = math.tan(math.radians(obliquity / 2)) ** 2
    long_rad = math.radians(mean_long)
    equation_of_time = (
        math.degrees(
            y * math.sin(2 * long_rad)
            - 2 * eccentricity * math.sin(anom)
            + 4 * eccentricity * y * math.sin(anom) * math.cos(2 * long_rad)
            - 0.5 * y * y * math.sin(4 * long_rad)
            - 1.25 * eccentricity * eccentricity * math.sin(2 * anom)
        )
        * 4
    )

    utc = when.astimezone(timezone.utc)
    minutes = utc.hour * 60 + utc.minute + utc.second / 60
    true_solar_time = (minutes + equation_of_time + 4 * longitude) % 1440
    hour_angle = true_solar_time / 4 - 180
    if hour_angle < -180:
        hour_angle += 360

    lat_rad = math.radians(latitude)
    dec_rad = math.radians(declination)
    ha_rad = math.radians(hour_angle)

    cos_zenith = math.sin(lat_rad) * math.sin(dec_rad) + math.cos(lat_rad) * math.cos(
        dec_rad
    ) * math.cos(ha_rad)
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    zenith = math.degrees(math.acos(cos_zenith))
    elevation = 90 - zenith

    sin_zenith = math.sin(math.radians(zenith))
    if abs(sin_zenith) < 1e-9 or abs(math.cos(lat_rad)) < 1e-9:
        azimuth = 180.0
    else:
        cos_az = (math.sin(lat_rad) * cos_zenith - math.sin(dec_rad)) / (
            math.cos(lat_rad) * sin_zenith
        )
        azimuth = math.degrees(math.acos(max(-1.0, min(1.0, cos_az))))
        azimuth = (180 + azimuth) % 360 if hour_angle > 0 else (180 - azimuth) % 360

    return SolarPosition(
        elevation=elevation,
        apparent_elevation=elevation + _refraction(elevation),
        azimuth=azimuth,
    )


def _refraction(elevation: float) -> float:
    """Saemundsson's atmospheric refraction correction, in degrees.

    Takes *geometric* altitude and returns the correction to add. Tables that
    quote a larger value at the same number are usually indexed by apparent
    altitude instead; at 2.4 deg the two conventions differ by about 0.02 deg.

    Only material below a few degrees, which is exactly where it matters: at
    2.4 deg geometric elevation it is 0.26 deg, and a 0.3 deg error there is
    an 11% error in the beam on a vertical surface.
    """
    if elevation < -0.575:
        return 0.0
    return 1.02 / math.tan(math.radians(elevation + 10.3 / (elevation + 5.11))) / 60


def fit_e0(samples: list[tuple[float, float]]) -> float | None:
    """Fit the extraterrestrial constant from the API's own data.

    `samples` is (terrestrial_radiation, cos_zenith) pairs, already filtered to
    high sun where our own solar position is trustworthy.

    `terrestrial_radiation` is extraterrestrial irradiance on the horizontal,
    i.e. E0 * cos(zenith), so the ratio recovers E0 whichever convention the
    API uses — flat 1361, or eccentricity-corrected. Fitting it per update
    means we never have to know which, and the fit also absorbs any constant
    offset between the API's solar position and ours.

    Returns None when the fit is not credible, in which case the caller should
    fall back to locally computed geometry rather than trust a wrong scale.
    """
    ratios = sorted(
        terrestrial / cos_zenith
        for terrestrial, cos_zenith in samples
        if cos_zenith > 0.0 and terrestrial > 0.0
    )
    if len(ratios) < E0_FIT_MIN_SAMPLES:
        return None

    middle = len(ratios) // 2
    median = (
        ratios[middle]
        if len(ratios) % 2
        else (ratios[middle - 1] + ratios[middle]) / 2
    )
    if not E0_MIN <= median <= E0_MAX:
        return None
    return median


def transpose(
    *,
    ghi: float,
    direct_horizontal: float,
    cos_zenith: float,
    solar_azimuth: float,
    apparent_elevation: float,
    tilt: float,
    surface_azimuth: float,
    albedo: float = DEFAULT_ALBEDO,
) -> PoaComponents:
    """Project irradiance onto a tilted plane, isotropic sky.

    `cos_zenith` should come from the API via `terrestrial_radiation` where
    available. All of the ill-conditioning in this function sits in the single
    division by it: on a vertical surface the beam term carries a factor of
    cot(elevation), which reaches 19 at 3 deg and 57 at 1 deg, so a half-degree
    error in solar position becomes a 25% error in the beam. sin(zenith),
    derived from the same cosine, is by contrast flat near the horizon and
    contributes no error worth naming.
    """
    diffuse = max(0.0, ghi - direct_horizontal)
    sin_zenith = math.sqrt(max(0.0, 1.0 - cos_zenith * cos_zenith))

    tilt_rad = math.radians(tilt)
    delta_azimuth = math.radians(solar_azimuth - surface_azimuth)
    cos_aoi = cos_zenith * math.cos(tilt_rad) + sin_zenith * math.sin(
        tilt_rad
    ) * math.cos(delta_azimuth)

    if (
        cos_aoi <= 0.0
        or cos_zenith <= 0.0
        or apparent_elevation < BEAM_FLOOR_ELEVATION
        or direct_horizontal <= 0.0
    ):
        beam = 0.0
        aoi = None if cos_aoi <= 0.0 else math.degrees(math.acos(min(1.0, cos_aoi)))
    else:
        beam = direct_horizontal * cos_aoi / cos_zenith
        aoi = math.degrees(math.acos(min(1.0, cos_aoi)))

    return PoaComponents(
        beam=beam,
        sky=diffuse * (1 + math.cos(tilt_rad)) / 2,
        ground=ghi * albedo * (1 - math.cos(tilt_rad)) / 2,
        aoi=aoi,
    )
