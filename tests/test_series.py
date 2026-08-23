"""Tests for the raw-block mechanism and solar sample assembly."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _loader import load  # noqa: E402

series = load("series")
solar = load("solar")

OSLO = (59.6, 10.75)
UTC_OFFSET = 7200
CEST = timezone(timedelta(seconds=UTC_OFFSET))


def _payload(
    *,
    start: datetime,
    count: int,
    step_minutes: int,
    e0: float = 1361.0,
    include_terrestrial: bool = True,
    ghi: float = 300.0,
    direct: float = 200.0,
) -> dict[str, list]:
    """Build one API-shaped block with self-consistent terrestrial radiation.

    Terrestrial radiation is generated as E0 * cos(zenith) at each sample's
    *midpoint*, which is what the API was measured to do, so a correct
    assembly must recover both E0 and cos(zenith) from it.
    """
    times: list[str] = []
    terrestrial: list[float] = []
    for index in range(count):
        timestamp = start + timedelta(minutes=step_minutes * index)
        times.append(timestamp.replace(tzinfo=None).isoformat())
        midpoint = timestamp + timedelta(minutes=step_minutes / 2)
        position = solar.solar_position(midpoint, *OSLO)
        terrestrial.append(e0 * position.cos_zenith)

    block: dict[str, list] = {
        "time": times,
        "shortwave_radiation_instant": [ghi] * count,
        "direct_radiation_instant": [direct] * count,
    }
    if include_terrestrial:
        block["terrestrial_radiation_instant"] = terrestrial
    return block


class TestExtractBlock(unittest.TestCase):
    """Lifting blocks out before the library gets to choke on them."""

    def test_whole_block_is_removed_when_no_fields_are_named(self) -> None:
        payload = {"minutely_15": _payload(
            start=datetime(2025, 6, 21, 12, tzinfo=CEST), count=4, step_minutes=15
        )}
        block = series.extract_block(payload, "minutely_15", None, UTC_OFFSET, 900)
        assert block is not None
        self.assertNotIn("minutely_15", payload)
        self.assertEqual(len(block.time), 4)
        self.assertIn("direct_radiation_instant", block.values)
        self.assertNotIn("time", block.values)

    def test_named_fields_are_removed_and_the_rest_left_behind(self) -> None:
        payload = {
            "hourly": {
                "time": ["2025-06-21T12:00"],
                "temperature_2m": [21.0],
                "direct_radiation_instant": [200.0],
            }
        }
        block = series.extract_block(
            payload, "hourly", ("direct_radiation_instant",), UTC_OFFSET, 3600
        )
        assert block is not None
        self.assertIn("hourly", payload)
        self.assertIn("temperature_2m", payload["hourly"])
        self.assertNotIn("direct_radiation_instant", payload["hourly"])
        self.assertEqual(block.values["direct_radiation_instant"], [200.0])

    def test_missing_or_malformed_blocks_are_tolerated(self) -> None:
        self.assertIsNone(series.extract_block({}, "minutely_15", None, UTC_OFFSET, 900))
        self.assertIsNone(
            series.extract_block({"hourly": {}}, "hourly", None, UTC_OFFSET, 3600)
        )
        self.assertIsNone(
            series.extract_block(
                {"hourly": {"time": ["2025-06-21T12:00"]}},
                "hourly",
                ("absent_field",),
                UTC_OFFSET,
                3600,
            )
        )

    def test_naive_timestamps_take_the_reported_utc_offset(self) -> None:
        payload = {"minutely_15": _payload(
            start=datetime(2025, 6, 21, 12, tzinfo=CEST), count=1, step_minutes=15
        )}
        block = series.extract_block(payload, "minutely_15", None, UTC_OFFSET, 900)
        assert block is not None
        self.assertEqual(block.time[0].utcoffset(), timedelta(seconds=UTC_OFFSET))
        self.assertEqual(block.time[0].hour, 12)

    def test_value_lookup_survives_short_and_absent_series(self) -> None:
        block = series.RawBlock(time=[], interval_seconds=900, values={"a": [1.0]})
        self.assertEqual(block.value("a", 0), 1.0)
        self.assertIsNone(block.value("a", 5))
        self.assertIsNone(block.value("missing", 0))


class TestBuildSolarData(unittest.TestCase):
    """Assembly, E0 fitting, and the horizon where the two blocks meet."""

    @staticmethod
    def _extras(**blocks: dict[str, list]) -> object:
        extras = series.RawExtras()
        for name, payload in blocks.items():
            interval = 900 if name == "minutely_15" else 3600
            holder = {name: payload}
            block = series.extract_block(holder, name, None, UTC_OFFSET, interval)
            assert block is not None
            extras.blocks[name] = block
        return extras

    def test_samples_land_on_interval_midpoints(self) -> None:
        data = series.build_solar_data(
            self._extras(
                hourly=_payload(
                    start=datetime(2025, 6, 21, 12, tzinfo=CEST), count=3, step_minutes=60
                )
            ),
            *OSLO,
        )
        self.assertEqual([s.instant.minute for s in data.samples], [30, 30, 30])
        self.assertEqual(data.samples[0].instant.hour, 12)

    def test_e0_is_recovered_whatever_convention_the_api_used(self) -> None:
        for e0 in (1361.0, 1316.0, 1407.0):
            data = series.build_solar_data(
                self._extras(
                    hourly=_payload(
                        start=datetime(2025, 6, 20, 0, tzinfo=CEST),
                        count=48,
                        step_minutes=60,
                        e0=e0,
                    )
                ),
                *OSLO,
            )
            assert data.e0 is not None
            self.assertAlmostEqual(data.e0, e0, delta=0.5, msg=f"E0={e0}")
            self.assertTrue(all(s.cos_zenith_from_api for s in data.samples))

    def test_api_cosine_matches_geometry_at_low_sun(self) -> None:
        # The whole point of sourcing cos(zenith) from the API is the range
        # where our own astronomy is amplified by 1/cos(zenith). Fitting E0 on
        # high sun must still land on the right cosine down at the horizon.
        data = series.build_solar_data(
            self._extras(
                hourly=_payload(
                    start=datetime(2025, 6, 20, 0, tzinfo=CEST),
                    count=48,
                    step_minutes=60,
                )
            ),
            *OSLO,
        )
        low = [s for s in data.samples if 0.0 < s.position.apparent_elevation < 5.0]
        self.assertGreater(len(low), 0)
        for sample in low:
            self.assertAlmostEqual(sample.cos_zenith, sample.position.cos_zenith, places=6)

    def test_implausible_scale_falls_back_to_local_geometry(self) -> None:
        data = series.build_solar_data(
            self._extras(
                hourly=_payload(
                    start=datetime(2025, 6, 20, 0, tzinfo=CEST),
                    count=48,
                    step_minutes=60,
                    e0=700.0,  # outside the credible band
                )
            ),
            *OSLO,
        )
        self.assertIsNone(data.e0)
        self.assertTrue(data.samples)
        self.assertFalse(any(s.cos_zenith_from_api for s in data.samples))

    def test_absent_terrestrial_field_falls_back_without_complaint(self) -> None:
        data = series.build_solar_data(
            self._extras(
                hourly=_payload(
                    start=datetime(2025, 6, 20, 0, tzinfo=CEST),
                    count=48,
                    step_minutes=60,
                    include_terrestrial=False,
                )
            ),
            *OSLO,
        )
        self.assertIsNone(data.e0)
        self.assertEqual(len(data.samples), 48)
        self.assertFalse(any(s.cos_zenith_from_api for s in data.samples))

    def test_hourly_does_not_double_up_on_the_quarter_hourly_span(self) -> None:
        # Hourly midpoints fall at :30 and quarter-hourly ones at :07:30 and
        # friends, so they never coincide — de-duplicating by timestamp would
        # silently keep both. The overlap has to be excluded by span.
        start = datetime(2025, 6, 21, 0, tzinfo=CEST)
        data = series.build_solar_data(
            self._extras(
                minutely_15=_payload(start=start, count=96, step_minutes=15),
                hourly=_payload(start=start, count=48, step_minutes=60),
            ),
            *OSLO,
        )
        self.assertEqual(len(data.samples), 96 + 24)
        self.assertEqual(len({s.instant for s in data.samples}), len(data.samples))
        self.assertEqual(
            [s.instant for s in data.samples],
            sorted(s.instant for s in data.samples),
        )

    def test_quarter_hourly_wins_inside_its_span_and_hourly_continues_after(self) -> None:
        start = datetime(2025, 6, 21, 0, tzinfo=CEST)
        data = series.build_solar_data(
            self._extras(
                minutely_15=_payload(start=start, count=96, step_minutes=15),
                hourly=_payload(start=start, count=48, step_minutes=60),
            ),
            *OSLO,
        )
        boundary = start + timedelta(hours=24)
        self.assertTrue(
            all(s.interval_seconds == 900 for s in data.samples if s.instant < boundary)
        )
        self.assertTrue(
            all(s.interval_seconds == 3600 for s in data.samples if s.instant > boundary)
        )

    def test_hourly_alone_is_used_when_there_is_no_quarter_hourly_block(self) -> None:
        data = series.build_solar_data(
            self._extras(
                hourly=_payload(
                    start=datetime(2025, 6, 21, 0, tzinfo=CEST), count=48, step_minutes=60
                )
            ),
            *OSLO,
        )
        self.assertEqual(len(data.samples), 48)

    def test_empty_extras_yield_no_samples(self) -> None:
        data = series.build_solar_data(series.RawExtras(), *OSLO)
        self.assertEqual(data.samples, [])
        self.assertIsNone(data.e0)

    def test_negative_irradiance_is_clamped(self) -> None:
        payload = _payload(
            start=datetime(2025, 6, 21, 12, tzinfo=CEST), count=2, step_minutes=60
        )
        payload["shortwave_radiation_instant"] = [-5.0, 300.0]
        payload["direct_radiation_instant"] = [-2.0, 200.0]
        data = series.build_solar_data(self._extras(hourly=payload), *OSLO)
        self.assertEqual(data.samples[0].ghi, 0.0)
        self.assertEqual(data.samples[0].direct_horizontal, 0.0)

    def test_null_irradiance_samples_are_dropped(self) -> None:
        payload = _payload(
            start=datetime(2025, 6, 21, 12, tzinfo=CEST), count=3, step_minutes=60
        )
        payload["direct_radiation_instant"] = [200.0, None, 200.0]
        data = series.build_solar_data(self._extras(hourly=payload), *OSLO)
        self.assertEqual(len(data.samples), 2)


class TestSolarDataLookup(unittest.TestCase):
    """Selecting the sample that describes a given moment."""

    def setUp(self) -> None:
        self.start = datetime(2025, 6, 21, 12, tzinfo=CEST)
        extras = TestBuildSolarData._extras(
            minutely_15=_payload(start=self.start, count=8, step_minutes=15)
        )
        self.data = series.build_solar_data(extras, *OSLO)

    def test_moment_inside_an_interval_selects_that_interval(self) -> None:
        sample = self.data.sample_at(self.start + timedelta(minutes=20))
        assert sample is not None
        self.assertEqual(sample.instant, self.start + timedelta(minutes=22, seconds=30))

    def test_interval_boundaries_belong_to_the_later_interval(self) -> None:
        sample = self.data.sample_at(self.start + timedelta(minutes=15))
        assert sample is not None
        self.assertEqual(sample.instant, self.start + timedelta(minutes=22, seconds=30))

    def test_moment_past_the_end_has_no_sample(self) -> None:
        self.assertIsNone(self.data.sample_at(self.start + timedelta(days=3)))

    def test_last_interval_is_still_covered_to_its_edge(self) -> None:
        last = self.data.samples[-1]
        edge = last.instant + timedelta(seconds=last.interval_seconds / 2 - 1)
        sample = self.data.sample_at(edge)
        assert sample is not None
        self.assertEqual(sample.instant, last.instant)

    def test_moment_before_the_start_has_no_sample(self) -> None:
        self.assertIsNone(self.data.sample_at(self.start - timedelta(days=1)))

    def test_upcoming_is_bounded_at_both_ends(self) -> None:
        upcoming = self.data.upcoming(self.start, timedelta(hours=1))
        self.assertTrue(upcoming)
        self.assertTrue(all(s.instant <= self.start + timedelta(hours=1) for s in upcoming))
        self.assertTrue(all(s.instant >= self.start for s in upcoming))


if __name__ == "__main__":
    unittest.main()
