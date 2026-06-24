#!/usr/bin/env python
# -*- coding: utf-8 -*-

from twisted.trial import unittest

from wfb_ng.tests.v6_formal_summary import (
    PAUSE_REASONS,
    READY_REJECTION_REASONS,
    SCENARIO_V6_NAMESPACE_DOWNLINK,
    SCENARIO_V6_NAMESPACE_UPLINK,
    SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SHARED,
    SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SINGLE,
    SCENARIO_V6_REAL_HARDWARE_UPLINK,
    SummaryValidationError,
    allowed_fields_for,
    build_summary,
)


class V6FormalSummaryTestCase(unittest.TestCase):
    def test_uplink_summary_rejects_non_2a_fields(self):
        summary = build_summary(
            SCENARIO_V6_NAMESPACE_UPLINK,
            tun_read_pause_total=3,
            tun_read_resume_total=3,
            tun_read_pause_total_by_reason={
                'queued_bytes_threshold': 2,
                'queued_packets_limit': 1,
            },
            reassembly_overflow_evict=0,
            unfinished_block_limit=40,
        )
        self.assertEqual(set(allowed_fields_for(SCENARIO_V6_NAMESPACE_UPLINK)), set(summary.keys()))

        with self.assertRaises(SummaryValidationError):
            build_summary(
                SCENARIO_V6_NAMESPACE_UPLINK,
                tun_read_pause_total=3,
                tun_read_resume_total=3,
                tun_read_pause_total_by_reason={
                    'queued_bytes_threshold': 2,
                    'queued_packets_limit': 1,
                },
                reassembly_overflow_evict=0,
                unfinished_block_limit=40,
                clients={},
            )

    def test_downlink_summary_uses_controlled_reason_sets(self):
        summary = build_summary(
            SCENARIO_V6_NAMESPACE_DOWNLINK,
            grant_sent_total=8,
            ready_accepted_total=3,
            ready_rejected_total_by_reason={reason: 0 for reason in READY_REJECTION_REASONS},
            feedback_window_open_count=2,
            feedback_window_close_count=2,
            feedback_uplink_hit_total_by_node={'1': 1, '2': 1},
            feedback_uplink_hit_total=2,
            tun_read_pause_total=5,
            tun_read_resume_total=5,
            tun_read_pause_total_by_reason={reason: 0 for reason in PAUSE_REASONS},
            reassembly_overflow_evict=0,
            unfinished_block_limit=40,
        )
        self.assertEqual(set(allowed_fields_for(SCENARIO_V6_NAMESPACE_DOWNLINK)), set(summary.keys()))

        with self.assertRaises(SummaryValidationError):
            build_summary(
                SCENARIO_V6_NAMESPACE_DOWNLINK,
                grant_sent_total=8,
                ready_accepted_total=3,
                ready_rejected_total_by_reason={
                    'invalid_source': 0,
                    'wrong_ingress_or_link_domain': 0,
                },
                feedback_window_open_count=2,
                feedback_window_close_count=2,
                feedback_uplink_hit_total_by_node={'1': 1},
                feedback_uplink_hit_total=1,
                tun_read_pause_total=5,
                tun_read_resume_total=5,
                tun_read_pause_total_by_reason={reason: 0 for reason in PAUSE_REASONS},
                reassembly_overflow_evict=0,
                unfinished_block_limit=40,
            )

    def test_real_hardware_summaries_share_schema_with_real_run_kind(self):
        uplink = build_summary(
            SCENARIO_V6_REAL_HARDWARE_UPLINK,
            tun_read_pause_total=0,
            tun_read_resume_total=0,
            tun_read_pause_total_by_reason={reason: 0 for reason in PAUSE_REASONS},
            reassembly_overflow_evict=0,
            unfinished_block_limit=0,
        )
        self.assertEqual('real_hardware', uplink['run_kind'])
        self.assertFalse(uplink['feedback_window_covered'])
        self.assertEqual(set(allowed_fields_for(SCENARIO_V6_REAL_HARDWARE_UPLINK)), set(uplink.keys()))

        single_downlink = build_summary(
            SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SINGLE,
            grant_sent_total=0,
            ready_accepted_total=0,
            ready_rejected_total_by_reason={reason: 0 for reason in READY_REJECTION_REASONS},
            tun_read_pause_total=0,
            tun_read_resume_total=0,
            tun_read_pause_total_by_reason={reason: 0 for reason in PAUSE_REASONS},
            reassembly_overflow_evict=0,
            unfinished_block_limit=0,
        )
        self.assertEqual('real_hardware', single_downlink['run_kind'])
        self.assertFalse(single_downlink['feedback_window_covered'])
        self.assertEqual(set(allowed_fields_for(SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SINGLE)), set(single_downlink.keys()))

        shared_downlink = build_summary(
            SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SHARED,
            grant_sent_total=0,
            ready_accepted_total=0,
            ready_rejected_total_by_reason={reason: 0 for reason in READY_REJECTION_REASONS},
            feedback_window_open_count=0,
            feedback_window_close_count=0,
            feedback_uplink_hit_total_by_node={},
            feedback_uplink_hit_total=0,
            tun_read_pause_total=0,
            tun_read_resume_total=0,
            tun_read_pause_total_by_reason={reason: 0 for reason in PAUSE_REASONS},
            reassembly_overflow_evict=0,
            unfinished_block_limit=0,
        )
        self.assertEqual('real_hardware', shared_downlink['run_kind'])
        self.assertTrue(shared_downlink['feedback_window_covered'])
        self.assertEqual(set(allowed_fields_for(SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SHARED)), set(shared_downlink.keys()))
