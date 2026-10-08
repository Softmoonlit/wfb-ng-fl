import unittest
from unittest import mock
from wfb_ng.fl.radio import (
    ALLOWED_MCS_VALUES,
    ALLOWED_5GHZ_CHANNELS,
    FORBIDDEN_CHANNELS,
    DEFAULT_CHANNEL,
    DEFAULT_TXPOWER_DBM,
    DEFAULT_DOWNLINK_MCS,
    RECOMMENDED_UPLINK_MCS,
    FIXED_FEC_K,
    FIXED_FEC_N,
    FIXED_BANDWIDTH,
    FIXED_GUARD_INTERVAL,
    RateBounds,
    get_downlink_rate_bounds,
    RadioConfig,
    validate_radio_config,
    validate_radio_patch,
    ChannelSurveyResult,
    SpectrumSurveyReport,
    MockSurveyBackend,
    LiveRadioSurveyBackend,
    find_wlx_interfaces,
    survey_spectrum,
    main,
)


class TestDownlinkRateBounds(unittest.TestCase):
    def test_constants_definitions(self):
        self.assertEqual(ALLOWED_5GHZ_CHANNELS, (149, 153, 157, 165))
        self.assertEqual(FORBIDDEN_CHANNELS, (161,))
        self.assertNotIn(161, ALLOWED_5GHZ_CHANNELS)
        self.assertEqual(DEFAULT_CHANNEL, 157)
        self.assertEqual(DEFAULT_TXPOWER_DBM, 12)
        self.assertEqual(DEFAULT_DOWNLINK_MCS, 3)
        self.assertEqual(RECOMMENDED_UPLINK_MCS, 6)
        self.assertEqual(ALLOWED_MCS_VALUES, (3, 4, 5, 6))
        self.assertEqual(FIXED_FEC_K, 8)
        self.assertEqual(FIXED_FEC_N, 14)
        self.assertEqual(FIXED_BANDWIDTH, "HT40+")
        self.assertEqual(FIXED_GUARD_INTERVAL, "short")

    def test_mcs3_rate_bounds(self):
        bounds = get_downlink_rate_bounds(3)
        self.assertEqual(bounds.downlink_mcs, 3)
        self.assertEqual(bounds.min_rate_kbps, 12000)
        self.assertEqual(bounds.max_rate_kbps, 18000)
        self.assertEqual(bounds.default_rate_kbps, 15000)
        self.assertEqual(bounds.min_rate_mbps, 12.0)
        self.assertEqual(bounds.max_rate_mbps, 18.0)
        self.assertEqual(bounds.default_rate_mbps, 15.0)

    def test_mcs4_rate_bounds(self):
        bounds = get_downlink_rate_bounds(4)
        self.assertEqual(bounds.downlink_mcs, 4)
        self.assertEqual(bounds.min_rate_kbps, 18000)
        self.assertEqual(bounds.max_rate_kbps, 26000)
        self.assertEqual(bounds.default_rate_kbps, 22000)

    def test_mcs5_rate_bounds(self):
        bounds = get_downlink_rate_bounds(5)
        self.assertEqual(bounds.downlink_mcs, 5)
        self.assertEqual(bounds.min_rate_kbps, 24000)
        self.assertEqual(bounds.max_rate_kbps, 35000)
        self.assertEqual(bounds.default_rate_kbps, 28000)

    def test_mcs6_rate_bounds(self):
        bounds = get_downlink_rate_bounds(6)
        self.assertEqual(bounds.downlink_mcs, 6)
        self.assertEqual(bounds.min_rate_kbps, 32000)
        self.assertEqual(bounds.max_rate_kbps, 45000)
        self.assertEqual(bounds.default_rate_kbps, 38000)

    def test_invalid_mcs_raises_value_error(self):
        for invalid_mcs in [0, 1, 2, 7, 8, 161, -1]:
            with self.assertRaises(ValueError):
                get_downlink_rate_bounds(invalid_mcs)


class TestRadioConfigValidator(unittest.TestCase):
    def test_default_config_valid(self):
        config = validate_radio_config({})
        self.assertEqual(config.channel, 157)
        self.assertEqual(config.radio_txpower_dbm, 12)
        self.assertEqual(config.downlink_mcs, 3)
        self.assertEqual(config.uplink_mcs, 6)
        self.assertEqual(config.uftp_rate_kbps, 15000)
        self.assertEqual(config.fec_k, 8)
        self.assertEqual(config.fec_n, 14)
        self.assertEqual(config.channel_width, "HT40+")
        self.assertEqual(config.guard_interval, "short")

    def test_channel_165_adapts_to_ht20(self):
        config157 = validate_radio_config({"channel": 157})
        self.assertEqual(config157.channel_width, "HT40+")
        config165 = validate_radio_config({"channel": 165})
        self.assertEqual(config165.channel_width, "HT20")

    def test_unknown_keys_fail_closed(self):
        with self.assertRaises(ValueError) as ctx:
            validate_radio_config({"channel": 157, "preset": "robust"})
        self.assertIn("preset", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            validate_radio_config({"channel": 157, "chanel": 157})
        self.assertIn("chanel", str(ctx.exception))

    def test_custom_valid_config(self):
        cfg_dict = {
            "channel": 149,
            "radio_txpower_dbm": 15,
            "downlink_mcs": 5,
            "uplink_mcs": 5,
            "uftp_rate_kbps": 30000,
        }
        config = validate_radio_config(cfg_dict)
        self.assertEqual(config.channel, 149)
        self.assertEqual(config.radio_txpower_dbm, 15)
        self.assertEqual(config.downlink_mcs, 5)
        self.assertEqual(config.uplink_mcs, 5)
        self.assertEqual(config.uftp_rate_kbps, 30000)

    def test_auto_fill_default_uftp_rate_when_omitted(self):
        config = validate_radio_config({"downlink_mcs": 4})
        self.assertEqual(config.uftp_rate_kbps, 22000)
        config5 = validate_radio_config({"downlink_mcs": 5})
        self.assertEqual(config5.uftp_rate_kbps, 28000)
        config6 = validate_radio_config({"downlink_mcs": 6})
        self.assertEqual(config6.uftp_rate_kbps, 38000)

    def test_channel_161_strictly_forbidden(self):
        with self.assertRaises(ValueError) as ctx:
            validate_radio_config({"channel": 161})
        self.assertIn("161", str(ctx.exception))
        self.assertTrue("forbidden" in str(ctx.exception).lower() or "crash" in str(ctx.exception).lower())

    def test_invalid_channel_rejected(self):
        for ch in [36, 40, 44, 48, 150, 160, 162, 166]:
            with self.assertRaises(ValueError):
                validate_radio_config({"channel": ch})

    def test_txpower_range_enforced(self):
        with self.assertRaises(ValueError):
            validate_radio_config({"radio_txpower_dbm": 9})
        with self.assertRaises(ValueError):
            validate_radio_config({"radio_txpower_dbm": 21})
        # 10 and 20 are inclusive boundaries
        c10 = validate_radio_config({"radio_txpower_dbm": 10})
        self.assertEqual(c10.radio_txpower_dbm, 10)
        c20 = validate_radio_config({"radio_txpower_dbm": 20})
        self.assertEqual(c20.radio_txpower_dbm, 20)

    def test_mcs_range_enforced(self):
        with self.assertRaises(ValueError):
            validate_radio_config({"downlink_mcs": 2})
        with self.assertRaises(ValueError):
            validate_radio_config({"downlink_mcs": 7})
        with self.assertRaises(ValueError):
            validate_radio_config({"uplink_mcs": 2})
        with self.assertRaises(ValueError):
            validate_radio_config({"uplink_mcs": 7})

    def test_uftp_rate_outside_bounds_rejected(self):
        # MCS 3 range is 12000..18000
        with self.assertRaises(ValueError):
            validate_radio_config({"downlink_mcs": 3, "uftp_rate_kbps": 11999})
        with self.assertRaises(ValueError):
            validate_radio_config({"downlink_mcs": 3, "uftp_rate_kbps": 18001})

        # MCS 6 range is 32000..45000
        with self.assertRaises(ValueError):
            validate_radio_config({"downlink_mcs": 6, "uftp_rate_kbps": 31999})
        with self.assertRaises(ValueError):
            validate_radio_config({"downlink_mcs": 6, "uftp_rate_kbps": 45001})

    def test_validate_radio_patch(self):
        base = RadioConfig(channel=157, radio_txpower_dbm=12, downlink_mcs=3, uplink_mcs=6, uftp_rate_kbps=15000)
        # Patch channel only: unmentioned attributes strictly preserved per ADR-0014
        patched = validate_radio_patch({"channel": 153}, base=base)
        self.assertEqual(patched.channel, 153)
        self.assertEqual(patched.radio_txpower_dbm, 12)
        self.assertEqual(patched.downlink_mcs, 3)
        self.assertEqual(patched.uplink_mcs, 6)
        self.assertEqual(patched.uftp_rate_kbps, 15000)

        # Patch txpower only
        patched_pwr = validate_radio_patch({"radio_txpower_dbm": 15}, base=base)
        self.assertEqual(patched_pwr.radio_txpower_dbm, 15)
        self.assertEqual(patched_pwr.channel, 157)

        # Patch downlink_mcs and uftp_rate explicitly
        patched_mcs = validate_radio_patch({"downlink_mcs": 5, "uftp_rate_kbps": 28000}, base=base)
        self.assertEqual(patched_mcs.downlink_mcs, 5)
        self.assertEqual(patched_mcs.uftp_rate_kbps, 28000)

        # Patch with unknown key fails closed
        with self.assertRaises(ValueError):
            validate_radio_patch({"unknown_field": 123}, base=base)

        # Patch with forbidden channel 161 rejected
        with self.assertRaises(ValueError):
            validate_radio_patch({"channel": 161}, base=base)


class TestSpectrumSurvey(unittest.TestCase):
    def test_survey_ranking_and_cleanliness(self):
        backend = MockSurveyBackend({
            149: 100,  # congested
            153: 0,    # perfectly clean -> rank 1
            157: 20,   # moderate -> rank 2
            165: 35,   # moderate -> rank 3
        })
        report = survey_spectrum(interface="wlx_mock", duration_ms=1000.0, backend=backend, congestion_threshold_fps=50.0)

        # Strictly 4 channels, strictly UNII-3 pool
        surveyed_channels = [r.channel for r in report.results]
        self.assertEqual(set(surveyed_channels), {149, 153, 157, 165})
        self.assertNotIn(161, surveyed_channels)

        # Ranked from cleanest (lowest fps) to most congested
        self.assertEqual(report.results[0].channel, 153)
        self.assertEqual(report.results[0].rank, 1)
        self.assertEqual(report.results[0].frame_count, 0)
        self.assertEqual(report.results[0].density_level, "clean")
        self.assertFalse(report.results[0].is_congested)

        self.assertEqual(report.results[1].channel, 157)
        self.assertEqual(report.results[1].rank, 2)
        self.assertEqual(report.results[1].frame_count, 20)

        self.assertEqual(report.results[2].channel, 165)
        self.assertEqual(report.results[2].rank, 3)
        self.assertEqual(report.results[2].frame_count, 35)

        self.assertEqual(report.results[3].channel, 149)
        self.assertEqual(report.results[3].rank, 4)
        self.assertEqual(report.results[3].frame_count, 100)
        self.assertTrue(report.results[3].is_congested)

        self.assertEqual(report.recommended_channel, 153)
        self.assertFalse(report.all_congested)
        self.assertIsNone(report.risk_warning)

    def test_survey_all_congested_triggers_risk_warning(self):
        backend = MockSurveyBackend({
            149: 60,
            153: 80,
            157: 75,
            165: 90,
        })
        report = survey_spectrum(interface="wlx_mock", duration_ms=1000.0, backend=backend, congestion_threshold_fps=50.0)

        self.assertTrue(report.all_congested)
        self.assertIsNotNone(report.risk_warning)
        self.assertIn("警告", report.risk_warning)
        self.assertIn("149", report.risk_warning)
        self.assertIn("165", report.risk_warning)
        # Still provides relative ranking
        self.assertEqual(report.results[0].channel, 149)
        self.assertEqual(report.recommended_channel, 149)

    def test_survey_candidate_pool_never_includes_channel_161(self):
        backend = MockSurveyBackend({
            149: 10,
            153: 20,
            157: 5,
            165: 15,
        })
        report = survey_spectrum(interface="wlx_mock", backend=backend)
        channels = [r.channel for r in report.results]
        self.assertEqual(channels, [157, 149, 165, 153])
        self.assertNotIn(161, channels)
        self.assertNotIn(161, backend.queried_channels)

    def test_live_backend_rejects_forbidden_channel_161(self):
        backend = LiveRadioSurveyBackend()
        with self.assertRaises(ValueError) as ctx:
            backend.count_frames("wlx_test", 161, 100.0)
        self.assertIn("161", str(ctx.exception))

    def test_mock_backend_rejects_forbidden_channel_161(self):
        backend = MockSurveyBackend()
        with self.assertRaises(ValueError) as ctx:
            backend.count_frames("wlx_test", 161, 100.0)
        self.assertIn("161", str(ctx.exception))

    def test_backend_prepare_and_finish_lifecycle(self):
        backend = MockSurveyBackend({149: 1, 153: 2, 157: 3, 165: 4})
        self.assertFalse(backend.prepared)
        self.assertFalse(backend.finished)
        survey_spectrum(interface="wlx_mock", backend=backend)
        self.assertTrue(backend.prepared)
        self.assertTrue(backend.finished)

    def test_survey_report_serialization(self):
        backend = MockSurveyBackend({149: 5, 153: 0, 157: 12, 165: 25})
        report = survey_spectrum(interface="wlx000", duration_ms=500.0, backend=backend)
        d = report.to_dict()
        self.assertEqual(d["interface"], "wlx000")
        self.assertEqual(d["recommended_channel"], 153)
        self.assertEqual(len(d["results"]), 4)
        self.assertIn("rank", d["results"][0])
        self.assertIn("fps", d["results"][0])

    def test_survey_raises_if_no_wlx_and_no_interface(self):
        with mock.patch("wfb_ng.fl.radio.find_wlx_interfaces", return_value=[]):
            with self.assertRaises(RuntimeError):
                survey_spectrum(interface=None, backend=None)


class TestRadioCLI(unittest.TestCase):
    def run_cli(self, args):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            code = main(args)
        return code, out_buf.getvalue(), err_buf.getvalue()

    def test_cli_rates_all(self):
        code, out, _ = self.run_cli(["rates"])
        self.assertEqual(code, 0)
        self.assertIn("MCS 3", out)
        self.assertIn("MCS 6", out)
        self.assertIn("15000", out)

    def test_cli_rates_specific_json(self):
        code, out, _ = self.run_cli(["rates", "--downlink-mcs", "4", "--json"])
        self.assertEqual(code, 0)
        import json
        data = json.loads(out)
        self.assertEqual(data["downlink_mcs"], 4)
        self.assertEqual(data["default_rate_kbps"], 22000)

    def test_cli_rates_invalid_mcs(self):
        code, out, err = self.run_cli(["rates", "--downlink-mcs", "2"])
        self.assertNotEqual(code, 0)

    def test_cli_validate_success(self):
        code, out, _ = self.run_cli([
            "validate",
            "--channel", "157",
            "--txpower", "12",
            "--downlink-mcs", "3",
            "--uplink-mcs", "6",
            "--json"
        ])
        self.assertEqual(code, 0)
        import json
        data = json.loads(out)
        self.assertEqual(data["channel"], 157)
        self.assertEqual(data["radio_txpower_dbm"], 12)
        self.assertEqual(data["downlink_mcs"], 3)
        self.assertEqual(data["uplink_mcs"], 6)
        self.assertEqual(data["uftp_rate_kbps"], 15000)

    def test_cli_validate_forbidden_channel_161(self):
        code, out, err = self.run_cli(["validate", "--channel", "161"])
        self.assertEqual(code, 1)
        combined = out + err
        self.assertIn("161", combined)

    def test_cli_validate_invalid_rate(self):
        code, out, err = self.run_cli([
            "validate",
            "--downlink-mcs", "3",
            "--uftp-rate", "30000"
        ])
        self.assertEqual(code, 1)

    def test_cli_survey_json(self):
        mock_report = SpectrumSurveyReport(
            interface="wlx_mock",
            results=[
                ChannelSurveyResult(153, 0, 300.0, 0.0, 1, False, "clean"),
                ChannelSurveyResult(157, 10, 300.0, 33.3, 2, False, "moderate"),
                ChannelSurveyResult(165, 20, 300.0, 66.7, 3, True, "congested"),
                ChannelSurveyResult(149, 30, 300.0, 100.0, 4, True, "congested"),
            ],
            recommended_channel=153,
            all_congested=False,
            risk_warning=None,
        )
        with mock.patch("wfb_ng.fl.radio.survey_spectrum", return_value=mock_report):
            code, out, _ = self.run_cli(["survey", "-i", "wlx_mock", "--json"])
        self.assertEqual(code, 0)
        import json
        data = json.loads(out)
        self.assertEqual(data["recommended_channel"], 153)
        self.assertFalse(data["all_congested"])
        self.assertEqual(len(data["results"]), 4)

    def test_cli_survey_congested_warning(self):
        mock_report = SpectrumSurveyReport(
            interface="wlx_mock",
            results=[
                ChannelSurveyResult(149, 80, 300.0, 266.7, 1, True, "busy"),
                ChannelSurveyResult(157, 85, 300.0, 283.3, 2, True, "busy"),
                ChannelSurveyResult(153, 90, 300.0, 300.0, 3, True, "busy"),
                ChannelSurveyResult(165, 100, 300.0, 333.3, 4, True, "busy"),
            ],
            recommended_channel=149,
            all_congested=True,
            risk_warning="警告：全频段（149, 153, 157, 165）检测到较强外部 802.11 帧密度干扰",
        )
        with mock.patch("wfb_ng.fl.radio.survey_spectrum", return_value=mock_report):
            code, out, _ = self.run_cli(["survey", "-i", "wlx_mock"])
        self.assertEqual(code, 0)
        self.assertIn("警告", out)
        self.assertIn("149", out)

    def test_external_process_invocation(self):
        import subprocess
        import sys
        # Verify python -m wfb_ng.fl.radio rates
        p1 = subprocess.run(
            [sys.executable, "-m", "wfb_ng.fl.radio", "rates", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(p1.returncode, 0)
        self.assertIn('"downlink_mcs": 3', p1.stdout)

        # Verify scripts/wfb-fl-radio validate
        p2 = subprocess.run(
            ["./scripts/wfb-fl-radio", "validate", "-c", "157", "-p", "12", "-d", "3", "-u", "6", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(p2.returncode, 0)
        self.assertIn('"channel": 157', p2.stdout)

        # Verify exit code 1 on forbidden channel 161
        p3 = subprocess.run(
            ["./scripts/wfb-fl-radio", "validate", "-c", "161"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(p3.returncode, 0)


if __name__ == "__main__":
    unittest.main()
