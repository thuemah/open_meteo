"""Pin the shipped Jinja macro against the Python transposition.

The macro is a copy of the same physics in another language, which is exactly
the kind of duplication that silently drifts. Home Assistant's template engine
is not available here, so the handful of globals the macro uses are supplied
directly; the macro body under test is the file that ships.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _loader import load  # noqa: E402

solar = load("solar")

try:
    import jinja2
except ImportError:  # pragma: no cover - exercised only where jinja2 is absent
    jinja2 = None

MACRO = Path(__file__).resolve().parents[1] / "custom_templates" / "open_meteo_solar.jinja"


@unittest.skipIf(jinja2 is None, "jinja2 is not installed")
class TestTemplateMacro(unittest.TestCase):
    """The macro must agree with transpose() wherever both are defined."""

    def setUp(self) -> None:
        environment = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(MACRO.parent)),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # Mirrors what Home Assistant exposes to templates.
        environment.globals.update(
            pi=math.pi,
            sin=math.sin,
            cos=math.cos,
            acos=math.acos,
        )
        environment.filters["to_json"] = json.dumps
        self.template = environment.from_string(
            "{% from 'open_meteo_solar.jinja' import poa %}"
            "{{ poa(tilt, surface_azimuth, ghi, direct_horizontal,"
            " sun_azimuth, sun_elevation, albedo) }}"
        )

    def _macro(self, **kwargs: float) -> dict[str, float | None]:
        kwargs.setdefault("albedo", solar.DEFAULT_ALBEDO)
        return json.loads(self.template.render(**kwargs))

    def _python(self, **kwargs: float) -> object:
        return solar.transpose(
            ghi=kwargs["ghi"],
            direct_horizontal=kwargs["direct_horizontal"],
            cos_zenith=math.sin(math.radians(kwargs["sun_elevation"])),
            solar_azimuth=kwargs["sun_azimuth"],
            apparent_elevation=kwargs["sun_elevation"],
            tilt=kwargs["tilt"],
            surface_azimuth=kwargs["surface_azimuth"],
            albedo=kwargs.get("albedo", solar.DEFAULT_ALBEDO),
        )

    CASES = (
        # A west wall through an evening, which is the case that motivated all
        # of this: high sun to the side, then low sun nearly normal to the face.
        dict(tilt=90, surface_azimuth=270, ghi=700, direct_horizontal=520,
             sun_azimuth=200, sun_elevation=48),
        dict(tilt=90, surface_azimuth=270, ghi=430, direct_horizontal=300,
             sun_azimuth=252, sun_elevation=25),
        dict(tilt=90, surface_azimuth=270, ghi=90, direct_horizontal=40,
             sun_azimuth=285, sun_elevation=6),
        dict(tilt=90, surface_azimuth=270, ghi=30, direct_horizontal=5,
             sun_azimuth=300, sun_elevation=2),
        # A flat roof, where the three components must sum back to GHI.
        dict(tilt=0, surface_azimuth=180, ghi=600, direct_horizontal=460,
             sun_azimuth=180, sun_elevation=50),
        # A pitched south roof.
        dict(tilt=35, surface_azimuth=180, ghi=550, direct_horizontal=400,
             sun_azimuth=175, sun_elevation=42),
        # Sun behind the plane.
        dict(tilt=90, surface_azimuth=270, ghi=500, direct_horizontal=380,
             sun_azimuth=90, sun_elevation=30),
        # Below the numerical floor.
        dict(tilt=90, surface_azimuth=270, ghi=12, direct_horizontal=0.4,
             sun_azimuth=292, sun_elevation=0.2),
        # Night.
        dict(tilt=90, surface_azimuth=270, ghi=0, direct_horizontal=0,
             sun_azimuth=10, sun_elevation=-8),
    )

    def test_components_agree_with_the_python_implementation(self) -> None:
        for case in self.CASES:
            rendered = self._macro(**case)
            expected = self._python(**case)
            label = f"{case['sun_elevation']} deg, az {case['sun_azimuth']}"
            self.assertAlmostEqual(rendered["beam"], expected.beam, delta=0.01, msg=label)
            self.assertAlmostEqual(rendered["sky"], expected.sky, delta=0.01, msg=label)
            self.assertAlmostEqual(rendered["ground"], expected.ground, delta=0.01, msg=label)
            self.assertAlmostEqual(rendered["total"], expected.total, delta=0.03, msg=label)

    def test_angle_of_incidence_agrees_including_its_absence(self) -> None:
        for case in self.CASES:
            rendered = self._macro(**case)
            expected = self._python(**case)
            if expected.aoi is None:
                self.assertIsNone(rendered["aoi"], msg=str(case))
            else:
                assert rendered["aoi"] is not None
                self.assertAlmostEqual(rendered["aoi"], expected.aoi, delta=0.02, msg=str(case))

    def test_flat_roof_reconstructs_global_horizontal(self) -> None:
        rendered = self._macro(
            tilt=0, surface_azimuth=180, ghi=600, direct_horizontal=460,
            sun_azimuth=180, sun_elevation=50,
        )
        self.assertAlmostEqual(rendered["total"], 600.0, delta=0.03)

    def test_albedo_default_matches_the_python_default(self) -> None:
        with_default = self._macro(
            tilt=90, surface_azimuth=180, ghi=400, direct_horizontal=250,
            sun_azimuth=180, sun_elevation=30,
        )
        explicit = self._macro(
            tilt=90, surface_azimuth=180, ghi=400, direct_horizontal=250,
            sun_azimuth=180, sun_elevation=30, albedo=solar.DEFAULT_ALBEDO,
        )
        self.assertEqual(with_default, explicit)


if __name__ == "__main__":
    unittest.main()
