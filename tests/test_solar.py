"""Tests for the solar geometry and transposition module.

Run with `python3 -m unittest discover -s tests` — no Home Assistant and no
third-party test runner needed, which is the point of keeping solar.py free of
hass imports.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _loader import load  # noqa: E402

solar = load("solar")

OSLO = (59.6, 10.75)
CEST = timezone(timedelta(hours=2))


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class TestSolarPosition(unittest.TestCase):
    """The astronomy, checked against facts that do not depend on our code."""

    def test_equinox_noon_elevation_is_ninety_minus_latitude(self) -> None:
        # At equinox the declination is ~0, so noon elevation is 90 - latitude.
        noon = _utc(2025, 3, 20, 10, 0)
        best = max(
            solar.solar_position(noon + timedelta(minutes=m), *OSLO).elevation
            for m in range(120)
        )
        self.assertAlmostEqual(best, 90 - OSLO[0], delta=0.5)

    def test_solstice_noon_elevation_adds_obliquity(self) -> None:
        start = _utc(2025, 6, 21, 9, 0)
        best = max(
            solar.solar_position(start + timedelta(minutes=m), *OSLO).elevation
            for m in range(180)
        )
        self.assertAlmostEqual(best, 90 - OSLO[0] + 23.44, delta=0.5)

    def test_azimuth_is_due_south_at_local_solar_noon(self) -> None:
        start = _utc(2025, 6, 21, 9, 0)
        highest = max(
            (solar.solar_position(start + timedelta(minutes=m), *OSLO) for m in range(180)),
            key=lambda p: p.elevation,
        )
        self.assertAlmostEqual(highest.azimuth, 180.0, delta=0.5)

    def test_azimuth_runs_east_to_west_through_the_day(self) -> None:
        morning = solar.solar_position(_utc(2025, 6, 21, 4, 0), *OSLO)
        evening = solar.solar_position(_utc(2025, 6, 21, 18, 0), *OSLO)
        self.assertLess(morning.azimuth, 180.0)
        self.assertGreater(evening.azimuth, 180.0)

    def test_result_does_not_depend_on_the_input_timezone(self) -> None:
        instant = _utc(2025, 6, 21, 20, 1)
        self.assertAlmostEqual(
            solar.solar_position(instant, *OSLO).elevation,
            solar.solar_position(instant.astimezone(CEST), *OSLO).elevation,
            places=9,
        )

    def test_naive_datetime_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            solar.solar_position(datetime(2025, 6, 21, 20, 1), *OSLO)


class TestRefraction(unittest.TestCase):
    """Refraction only matters near the horizon — but there it matters a lot."""

    def test_magnitude_near_the_horizon(self) -> None:
        # The input is *geometric* altitude, so 0.26 deg at 2.4 deg geometric —
        # published tables quoting ~0.28 deg there are indexed by apparent
        # altitude, which is a different argument. Getting this wrong by
        # 0.3 deg is an 11% error in the beam on a vertical surface, so the
        # value is pinned rather than left to drift with a formula swap.
        self.assertAlmostEqual(solar._refraction(2.4), 0.26, delta=0.01)

    def test_negligible_at_high_sun(self) -> None:
        self.assertLess(solar._refraction(45.0), 0.02)

    def test_apparent_elevation_is_never_below_geometric(self) -> None:
        for hour in range(24):
            position = solar.solar_position(_utc(2025, 6, 21, hour), *OSLO)
            self.assertGreaterEqual(position.apparent_elevation, position.elevation)


class TestSampleInstant(unittest.TestCase):
    """The measured timestamp convention: value belongs to the midpoint."""

    def test_quarter_hour_offsets_by_seven_and_a_half_minutes(self) -> None:
        self.assertEqual(
            solar.sample_instant(_utc(2025, 6, 21, 20, 15), 900),
            _utc(2025, 6, 21, 20, 22) + timedelta(seconds=30),
        )

    def test_hourly_offsets_by_half_an_hour(self) -> None:
        self.assertEqual(
            solar.sample_instant(_utc(2025, 6, 21, 20, 0), 3600),
            _utc(2025, 6, 21, 20, 30),
        )


class TestFitE0(unittest.TestCase):
    """Fitting E0 is what makes the undocumented convention a non-question."""

    @staticmethod
    def _samples(e0: float, count: int = 40) -> list[tuple[float, float]]:
        return [(e0 * (0.4 + i / 100), 0.4 + i / 100) for i in range(count)]

    def test_recovers_a_flat_solar_constant(self) -> None:
        self.assertAlmostEqual(solar.fit_e0(self._samples(1361.0)), 1361.0, places=6)

    def test_recovers_an_eccentricity_corrected_value(self) -> None:
        for e0 in (1316.0, 1407.0):
            self.assertAlmostEqual(solar.fit_e0(self._samples(e0)), e0, places=6)

    def test_median_shrugs_off_outliers(self) -> None:
        samples = self._samples(1361.0)
        samples[0] = (99999.0, 0.5)
        samples[1] = (0.001, 0.5)
        self.assertAlmostEqual(solar.fit_e0(samples), 1361.0, delta=15.0)

    def test_rejects_a_fit_outside_the_credible_band(self) -> None:
        self.assertIsNone(solar.fit_e0(self._samples(900.0)))
        self.assertIsNone(solar.fit_e0(self._samples(2000.0)))

    def test_rejects_too_few_samples(self) -> None:
        self.assertIsNone(solar.fit_e0(self._samples(1361.0, count=4)))

    def test_ignores_night_time_samples(self) -> None:
        samples = self._samples(1361.0) + [(0.0, 0.0)] * 50
        self.assertAlmostEqual(solar.fit_e0(samples), 1361.0, places=6)


class TestTranspose(unittest.TestCase):
    """Transposition, and the properties that must hold for it to be usable."""

    def test_horizontal_plane_reconstructs_global_horizontal(self) -> None:
        # The strongest available identity: at tilt 0 the three components must
        # sum back to GHI exactly, whatever the sun is doing.
        for cos_zenith in (0.05, 0.3, 0.9):
            result = solar.transpose(
                ghi=500.0,
                direct_horizontal=380.0,
                cos_zenith=cos_zenith,
                solar_azimuth=210.0,
                apparent_elevation=math.degrees(math.asin(cos_zenith)),
                tilt=0.0,
                surface_azimuth=180.0,
            )
            self.assertAlmostEqual(result.total, 500.0, places=9)
            self.assertAlmostEqual(result.ground, 0.0, places=9)

    def test_vertical_plane_facing_the_sun_scales_by_cotangent(self) -> None:
        elevation = 3.0
        cos_zenith = math.sin(math.radians(elevation))
        result = solar.transpose(
            ghi=60.0,
            direct_horizontal=10.0,
            cos_zenith=cos_zenith,
            solar_azimuth=270.0,
            apparent_elevation=elevation,
            tilt=90.0,
            surface_azimuth=270.0,
        )
        self.assertAlmostEqual(result.beam, 10.0 / math.tan(math.radians(elevation)), places=6)

    def test_sun_behind_the_plane_gives_no_beam_and_no_angle(self) -> None:
        result = solar.transpose(
            ghi=600.0,
            direct_horizontal=450.0,
            cos_zenith=0.7,
            solar_azimuth=90.0,
            apparent_elevation=44.4,
            tilt=90.0,
            surface_azimuth=270.0,
        )
        self.assertEqual(result.beam, 0.0)
        self.assertIsNone(result.aoi)
        self.assertGreater(result.sky, 0.0)

    def test_numerical_floor_kills_the_beam_but_leaves_the_sky(self) -> None:
        # Below the floor direct_radiation is quantisation noise multiplied by
        # more than a hundred. The sky term is unaffected and must survive —
        # that separation is the whole reason components are kept apart.
        result = solar.transpose(
            ghi=12.0,
            direct_horizontal=0.4,
            cos_zenith=math.sin(math.radians(0.2)),
            solar_azimuth=272.0,
            apparent_elevation=0.2,
            tilt=90.0,
            surface_azimuth=270.0,
        )
        self.assertEqual(result.beam, 0.0)
        self.assertAlmostEqual(result.sky, 11.6 / 2, places=6)

    def test_vertical_sky_view_is_half_and_ground_view_is_half(self) -> None:
        result = solar.transpose(
            ghi=400.0,
            direct_horizontal=300.0,
            cos_zenith=0.6,
            solar_azimuth=180.0,
            apparent_elevation=36.87,
            tilt=90.0,
            surface_azimuth=180.0,
            albedo=0.2,
        )
        self.assertAlmostEqual(result.sky, 100.0 / 2, places=9)
        self.assertAlmostEqual(result.ground, 400.0 * 0.2 / 2, places=9)

    def test_components_are_never_negative(self) -> None:
        result = solar.transpose(
            ghi=100.0,
            direct_horizontal=140.0,  # inconsistent input; must not go negative
            cos_zenith=0.5,
            solar_azimuth=180.0,
            apparent_elevation=30.0,
            tilt=90.0,
            surface_azimuth=180.0,
        )
        self.assertGreaterEqual(result.sky, 0.0)
        self.assertGreaterEqual(result.beam, 0.0)
        self.assertGreaterEqual(result.ground, 0.0)


class TestWestFacadeGeometry(unittest.TestCase):
    """Regression over the geometry that motivated all of this.

    Pins solar_position and transpose together against hand-checked values for
    a vertical west-facing wall at 59.6N on the June solstice. The angle of
    incidence *falls* through the evening as the sun swings west — the
    opposite of what low sun elevation might suggest — which is why incidence
    angle losses are not a meaningful error source for vertical glazing.
    """

    EXPECTED = {  # local CEST hour -> (solar azimuth, apparent elevation, AOI)
        14: (195.8, 53.1, 80.6),
        16: (235.8, 44.2, 53.6),
        18: (265.6, 30.0, 30.3),
        20: (290.8, 15.1, 25.5),
        22: (315.5, 2.7, 45.6),
    }

    def test_matches_hand_checked_angles(self) -> None:
        for hour, (azimuth, elevation, aoi) in self.EXPECTED.items():
            when = datetime(2025, 6, 21, hour, tzinfo=CEST)
            position = solar.solar_position(when, *OSLO)
            self.assertAlmostEqual(position.azimuth, azimuth, delta=0.15, msg=f"{hour}:00 azimuth")
            self.assertAlmostEqual(
                position.apparent_elevation, elevation, delta=0.15, msg=f"{hour}:00 elevation"
            )
            result = solar.transpose(
                ghi=400.0,
                direct_horizontal=200.0,
                cos_zenith=position.cos_zenith,
                solar_azimuth=position.azimuth,
                apparent_elevation=position.apparent_elevation,
                tilt=90.0,
                surface_azimuth=270.0,
            )
            self.assertIsNotNone(result.aoi, msg=f"{hour}:00 sun should be on the west face")
            assert result.aoi is not None
            self.assertAlmostEqual(result.aoi, aoi, delta=0.3, msg=f"{hour}:00 AOI")

    def test_incidence_angle_falls_from_the_afternoon_into_the_evening(self) -> None:
        angles = [self.EXPECTED[h][2] for h in (14, 16, 18)]
        self.assertEqual(angles, sorted(angles, reverse=True))


if __name__ == "__main__":
    unittest.main()
