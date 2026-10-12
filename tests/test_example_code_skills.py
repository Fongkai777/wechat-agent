"""Portable task examples stay independent of the platform's business logic."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from wechat_agent.code_skill_packages import API_DOC, validate_package
from wechat_agent.code_skill_runtime import run_package
from wechat_agent.code_skill_service import validate_code_skill
from wechat_agent.goals import GoalStore


def example_package(name):
    root = Path(__file__).resolve().parents[1] / 'examples' / 'skills' / name
    return validate_package({
        'schema_version': 3, 'name': name, 'description': 'Editable task example',
        'files': [{'path': path.name, 'content': path.read_text()}
                  for path in sorted(root.iterdir()) if path.suffix in ('.py', '.json', '.md')]
                 + [{'path': 'PLATFORM_API.md', 'content': API_DOC}],
    })


class ExamplePackageTests(unittest.TestCase):
    def test_packages_are_valid(self):
        for name in ('unanswered', 'internships'):
            self.assertEqual(example_package(name)['schema_version'], 3)


@unittest.skipUnless(os.environ.get('TEST_CODE_SKILL_SANDBOX') == '1', 'Explicit sandbox integration test')
class ExampleSandboxTests(unittest.TestCase):
    def test_package_regressions_and_host_integration(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory) / 'goals.sqlite3')
            for name in ('unanswered', 'internships'):
                with self.subTest(name=name):
                    report = validate_code_skill(example_package(name), 'Synthetic task', store)
                    self.assertTrue(report['passed'])

    def test_1200_private_chats_fit_real_rpc_budget(self):
        chats = [{'id':str(i), 'title':'Synthetic', 'type':'private'} for i in range(1200)]
        calls = []
        def dispatch(method, args):
            calls.append(method)
            if method == 'log': return None
            if method == 'chats': return chats
            if method == 'messages_batch':
                return {chat:[{'chat_id':chat, 'sender_username':'other', 'text':'Hi'}]
                        for chat in args['chat_ids']}
            if method == 'cite':
                return [{**row, 'reference':i + 1} for i, row in enumerate(args['messages'])]
            raise AssertionError(method)
        result = run_package(example_package('unanswered'), {'account':'self'}, dispatch)
        self.assertEqual(len(result['items']), 1200)
        self.assertEqual(len(calls), 15)
