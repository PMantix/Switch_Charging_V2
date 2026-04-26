"""Tests for the Neware XML → schedule JSON importer."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

from tools.neware_to_json import (
    convert_steps,
    neware_to_schedule_json,
    parse_neware_xml,
)


def _write_xml(tmp: Path, body: str) -> Path:
    """Wrap a Step_Info body in the Neware envelope and write to disk."""
    xml = dedent(f"""\
        <?xml version="1.0" encoding="utf-8"?>
        <root>
          <config type="Step File" version="18">
            <Step_Info Num="0">
        {body}
            </Step_Info>
          </config>
        </root>
        """)
    p = tmp / "test.xml"
    p.write_text(xml)
    return p


class TestParser(unittest.TestCase):

    def test_parse_extracts_step_id_and_type(self):
        with TemporaryDirectory() as d:
            xml = _write_xml(Path(d), """\
              <Step1 Step_ID="1" Step_Type="4">
                <Limit><Main><Time Value="60000" /></Main></Limit>
              </Step1>
            """)
            steps = parse_neware_xml(xml)
            self.assertEqual(len(steps), 1)
            self.assertEqual(steps[0].step_id, 1)
            self.assertEqual(steps[0].step_type, 4)
            self.assertEqual(steps[0].time_ms, 60000)

    def test_parse_extracts_curr_volt_stop_fields(self):
        with TemporaryDirectory() as d:
            xml = _write_xml(Path(d), """\
              <Step1 Step_ID="1" Step_Type="7">
                <Limit><Main>
                  <Curr Value="13.6" />
                  <Volt Value="43000" />
                  <Time Value="180000000" />
                  <Stop_Curr Value="2" />
                </Main></Limit>
              </Step1>
            """)
            ns = parse_neware_xml(xml)[0]
            self.assertEqual(ns.curr_ma, 13.6)
            self.assertEqual(ns.volt_01mV, 43000)
            self.assertEqual(ns.stop_curr_ma, 2.0)
            self.assertEqual(ns.time_ms, 180000000)

    def test_missing_step_info_raises(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "bad.xml"
            p.write_text(
                '<?xml version="1.0"?><root><config></config></root>'
            )
            with self.assertRaises(ValueError):
                parse_neware_xml(p)


class TestStepConversion(unittest.TestCase):

    def test_type1_maps_to_cc_charge(self):
        from tools.neware_to_json import _NewareStep
        steps = [_NewareStep(step_id=1, step_type=1, time_ms=3600000,
                             curr_ma=5.43, stop_volt_01mV=38000)]
        warnings = []
        out = convert_steps(steps, warnings)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["expected_state"], "cc_charge")
        self.assertEqual(out[0]["circuit_action"], "charge")
        self.assertEqual(out[0]["timeout_s"], 3600.0)
        self.assertEqual(out[0]["neware_meta"]["neware_curr_a"], 0.00543)
        self.assertEqual(out[0]["neware_meta"]["neware_stop_volt_v"], 3.8)

    def test_type2_maps_to_discharge(self):
        from tools.neware_to_json import _NewareStep
        out = convert_steps(
            [_NewareStep(step_id=1, step_type=2, time_ms=18000000,
                         curr_ma=190.2, stop_volt_01mV=26500)],
            [],
        )
        self.assertEqual(out[0]["expected_state"], "discharge")
        self.assertEqual(out[0]["circuit_action"], "discharge")
        self.assertEqual(out[0]["neware_meta"]["neware_stop_volt_v"], 2.65)

    def test_type4_maps_to_rest(self):
        from tools.neware_to_json import _NewareStep
        out = convert_steps(
            [_NewareStep(step_id=1, step_type=4, time_ms=600000)],
            [],
        )
        self.assertEqual(out[0]["expected_state"], "rest")
        self.assertEqual(out[0]["circuit_action"], "idle")
        self.assertEqual(out[0]["timeout_s"], 600.0)
        # Rest steps have no current/voltage metadata
        self.assertNotIn("neware_meta", out[0])

    def test_type7_maps_to_cc_charge_with_cv_target(self):
        from tools.neware_to_json import _NewareStep
        out = convert_steps(
            [_NewareStep(step_id=1, step_type=7, time_ms=180000000,
                         curr_ma=13.6, volt_01mV=43000, stop_curr_ma=2.0)],
            [],
        )
        # CCCV is collapsed into a single PLAN step with cc_charge —
        # divergence indicator surfaces the CV phase.
        self.assertEqual(out[0]["expected_state"], "cc_charge")
        self.assertEqual(out[0]["neware_meta"]["neware_cv_target_v"], 4.3)
        self.assertEqual(out[0]["neware_meta"]["neware_stop_curr_a"], 0.002)

    def test_type6_terminates_step_list(self):
        from tools.neware_to_json import _NewareStep
        steps = [
            _NewareStep(step_id=1, step_type=4, time_ms=1000),
            _NewareStep(step_id=2, step_type=6),
            _NewareStep(step_id=3, step_type=4, time_ms=1000),  # ignored
        ]
        out = convert_steps(steps, [])
        self.assertEqual(len(out), 1)

    def test_unknown_step_type_warns_and_skips(self):
        from tools.neware_to_json import _NewareStep
        warnings = []
        out = convert_steps(
            [_NewareStep(step_id=1, step_type=99, time_ms=1000)],
            warnings,
        )
        self.assertEqual(len(out), 0)
        self.assertEqual(len(warnings), 1)
        self.assertIn("Step_Type=99", warnings[0])

    def test_missing_time_uses_default_with_warning(self):
        from tools.neware_to_json import _NewareStep
        warnings = []
        out = convert_steps(
            [_NewareStep(step_id=1, step_type=1, curr_ma=5.0,
                         stop_volt_01mV=38000)],
            warnings,
        )
        self.assertEqual(out[0]["timeout_s"], 600.0)
        self.assertEqual(len(warnings), 1)
        self.assertIn("Time", warnings[0])


class TestStepNaming(unittest.TestCase):

    def test_cc_charge_name_includes_curr_and_stop_volt(self):
        from tools.neware_to_json import _NewareStep
        out = convert_steps(
            [_NewareStep(step_id=1, step_type=1, time_ms=36000000,
                         curr_ma=5.43, stop_volt_01mV=38000)],
            [],
        )
        name = out[0]["name"]
        self.assertIn("CC Charge", name)
        self.assertIn("5.43mA", name)
        self.assertIn("3.80V", name)

    def test_rest_name_includes_duration(self):
        from tools.neware_to_json import _NewareStep
        out = convert_steps([
            _NewareStep(step_id=1, step_type=4, time_ms=600000),
            _NewareStep(step_id=2, step_type=4, time_ms=7200000),
            _NewareStep(step_id=3, step_type=4, time_ms=72000000),
        ], [])
        self.assertIn("10min", out[0]["name"])
        self.assertIn("2h", out[1]["name"])
        self.assertIn("20h", out[2]["name"])

    def test_cccv_name_shows_cv_target(self):
        from tools.neware_to_json import _NewareStep
        out = convert_steps(
            [_NewareStep(step_id=1, step_type=7, time_ms=180000000,
                         curr_ma=13.6, volt_01mV=43000, stop_curr_ma=2.0)],
            [],
        )
        self.assertIn("CCCV", out[0]["name"])
        self.assertIn("13.60mA", out[0]["name"])
        self.assertIn("CV@4.30V", out[0]["name"])


class TestEndToEnd(unittest.TestCase):
    """The full pipeline: XML in → valid schedule JSON out → loads via
    schedule.load_schedule without errors."""

    def test_full_pipeline_against_fixture_xml(self):
        # Run a tiny synthetic schedule end-to-end.
        with TemporaryDirectory() as d:
            xml = _write_xml(Path(d), """\
              <Step1 Step_ID="1" Step_Type="4">
                <Limit><Main><Time Value="60000" /></Main></Limit>
              </Step1>
              <Step2 Step_ID="2" Step_Type="1">
                <Limit><Main>
                  <Curr Value="5.43" />
                  <Time Value="3600000" />
                  <Stop_Volt Value="38000" />
                </Main></Limit>
              </Step2>
              <Step3 Step_ID="3" Step_Type="2">
                <Limit><Main>
                  <Curr Value="190.2" />
                  <Time Value="1800000" />
                  <Stop_Volt Value="26500" />
                </Main></Limit>
              </Step3>
              <Step4 Step_ID="4" Step_Type="6"/>
            """)
            sched, warnings = neware_to_schedule_json(xml, name="Test")
            self.assertEqual(sched["name"], "Test")
            self.assertEqual(len(sched["steps"]), 3)  # End step dropped
            self.assertEqual(warnings, [])

            # Write to a file and load via the production loader.
            out_path = Path(d) / "out.json"
            out_path.write_text(json.dumps(sched))
            from server.schedule import load_schedule
            loaded = load_schedule(out_path)
            self.assertEqual(len(loaded.steps), 3)
            self.assertEqual(loaded.steps[0].expected_state, "rest")
            self.assertEqual(loaded.steps[1].expected_state, "cc_charge")
            self.assertEqual(loaded.steps[2].expected_state, "discharge")

    def test_real_world_formation_xml_if_available(self):
        """Sanity-check against the user's actual formation cycle XML.
        Skipped if the file isn't present (e.g., in CI)."""
        path = Path(
            "/Users/phillipaquino/Documents/HRI-OH/Switching_Electrode/"
            "TestSchedules/Neware/Switching_Electrode_Formation_Cycle.xml"
        )
        if not path.exists():
            self.skipTest(f"Real-world XML not at {path}")

        sched, warnings = neware_to_schedule_json(path)
        # 17 steps in the XML, last is END → 16 in output
        self.assertEqual(len(sched["steps"]), 16)
        self.assertEqual(warnings, [])

        # Sanity-check the CCCV step (Step 11 in the XML, index 10 in output)
        cccv = sched["steps"][10]
        self.assertEqual(cccv["expected_state"], "cc_charge")
        self.assertEqual(cccv["neware_meta"]["neware_cv_target_v"], 4.3)
        self.assertEqual(cccv["neware_meta"]["neware_stop_curr_a"], 0.002)

        # Sanity-check the 10-hour low-rate CC step (Step 3, index 2)
        cc1 = sched["steps"][2]
        self.assertEqual(cc1["expected_state"], "cc_charge")
        self.assertEqual(cc1["timeout_s"], 36000.0)  # 10 hours
        self.assertEqual(cc1["neware_meta"]["neware_curr_a"], 0.00543)


if __name__ == "__main__":
    unittest.main()
