import threading
import unittest

from wfb_ng.fl.algorithm import (AlgorithmContext, freeze_algorithm_config, load_algorithm,
                                 validate_evaluation_result)
from wfb_ng.fl.errors import FLRuntimeError


class AlgorithmContextTests(unittest.TestCase):
    def context(self, **kwargs):
        return AlgorithmContext(job_id='job-a', node_id=1, target_nodes=(1, 2, 4),
                                rounds=1, artifact_dir='/tmp/artifacts',
                                local_config={'mnist_data_dir': '/data/mnist'},
                                stop_event=threading.Event(), **kwargs)

    def test_phase_views_share_stop_and_keep_host_facts_read_only(self):
        context = self.context(initial_metrics={'loss': 2.0, 'preparation': {'samples': 10000}})
        phase = context.for_phase('training', round_index=1)
        self.assertEqual(phase.round_index, 1)
        self.assertEqual(context.round_index, 0)
        self.assertEqual(phase.initial_metrics['loss'], 2.0)
        with self.assertRaises(TypeError):
            phase.initial_metrics['preparation']['samples'] = 1
        with self.assertRaises(TypeError):
            phase.local_config['mnist_data_dir'] = '/other'
        context.stop_event.set()
        with self.assertRaises(FLRuntimeError) as caught:
            phase.check_stop()
        self.assertEqual(caught.exception.error_code, 'job_aborted')

    def test_reports_wait_for_host_acceptance_and_do_not_mutate_caller(self):
        accepted = []
        prepared = {'model_format': 'reference', 'files': {'images': 'abc'}}
        context = self.context(prepared_callback=lambda report: accepted.append(report),
                               round_callback=lambda index: accepted.append(index))
        context.report_prepared(prepared)
        context.report_round_complete(1)
        prepared['files']['images'] = 'changed'
        self.assertEqual(accepted, [{'model_format': 'reference', 'files': {'images': 'abc'}}, 1])

    def test_missing_or_invalid_report_channel_fails_explicitly(self):
        with self.assertRaises(FLRuntimeError):
            self.context().report_prepared({})
        context = self.context(prepared_callback=lambda report: None)
        for report in ({'loss': float('nan')}, {'reason': 'a' * 4096}):
            with self.subTest(report_type=type(report).__name__), self.assertRaises(FLRuntimeError):
                context.report_prepared(report)
        with self.assertRaises(FLRuntimeError):
            self.context(round_callback=lambda index: None).report_round_complete(True)

    def test_frozen_configuration_and_evaluation_contract(self):
        config = freeze_algorithm_config({'seed': 7, 'options': {'shuffle': True}})
        with self.assertRaises(TypeError):
            config['options']['shuffle'] = False
        result = {'metrics': {'loss': 1.2}, 'preparation': None,
                  'verdict': False, 'reason': '未达目标'}
        self.assertEqual(validate_evaluation_result(result), result)
        for value in ({**result, 'metrics': {'loss': float('nan')}},
                      {**result, 'verdict': 1}, {**result, 'metrics': {}},
                      {**result, 'reason': ''}, {**result, 'reason': '字' * 171}):
            with self.assertRaises(FLRuntimeError):
                validate_evaluation_result(value)

    def test_callable_loading_rejects_obsolete_signature(self):
        self.assertIs(load_algorithm('wfb_ng.fl.algorithm:load_algorithm', arity=1), load_algorithm)
        with self.assertRaises(FLRuntimeError):
            load_algorithm('wfb_ng.fl.algorithm:load_algorithm', arity=3)
        with self.assertRaises(FLRuntimeError):
            load_algorithm('wfb_ng.fl.algorithm:AlgorithmContext.missing')
