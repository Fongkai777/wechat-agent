import http.client
import unittest
from unittest.mock import Mock, patch

from wechat_agent import web
from wechat_agent.goal_checkpoints import GoalResumeRequired


class RetrievalNetworkTests(unittest.TestCase):
    def test_connection_errors_are_readable_without_retry_or_private_body(self):
        for error in (ConnectionResetError('private body'),
                      http.client.IncompleteRead(b'private body'),
                      http.client.RemoteDisconnected('private body')):
            with self.subTest(error=type(error).__name__), patch.object(
                    web.urllib.request, 'urlopen', side_effect=error) as send:
                with self.assertRaisesRegex(RuntimeError, '连接中断') as raised:
                    web.call_chat_payload({'model': 'fixture', 'base_url': 'https://example.test'}, 'fixture', [])
                self.assertNotIn('private body', str(raised.exception))
                self.assertEqual(send.call_count, 1)

    def test_rerank_timeout_falls_back_but_uncertain_retries_require_consent(self):
        config = {'qa': {}, 'rerank': {'enabled': True, 'batch_size': 10}}
        items = [{'text': 'fixture'}]
        state = Mock()
        with patch.object(web, 'load_llm_config', return_value=config), \
             patch.object(web, 'effective_llm_profile', return_value={}), \
             patch.object(web, 'resolve_llm_api_key', return_value='fixture'), \
             patch.object(web, 'call_chat_completion', side_effect=TimeoutError('timeout')) as send:
            result, diagnostics = web.rerank_qa_items_with_config(state, 'question', items, 1)
            self.assertEqual(result, items)
            self.assertFalse(diagnostics['rerank_used'])
            self.assertEqual(diagnostics['rerank_error'], 'timeout')
            send.side_effect = GoalResumeRequired('confirmation required')
            with self.assertRaises(GoalResumeRequired):
                web.rerank_qa_items_with_config(state, 'question', items, 1)


if __name__ == '__main__':
    unittest.main()
