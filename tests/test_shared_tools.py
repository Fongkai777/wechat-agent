import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_code_skills import package
from tests.test_qa_agent import decision, completion
from wechat_agent.code_skill_service import synthetic_api, generate_code_skill, run_code_skill
from wechat_agent.capabilities import assess_capabilities, capability_catalog
from wechat_agent.goals import GoalStore
from wechat_agent.platform_tools import FUNCTION_TOOLS


def report(status='supported',capabilities=None):
    return {'summary':'检查任务能力','requirements':[{'need':'检查私聊','status':status,
        'capabilities':['messages.read','python.compute'] if capabilities is None else capabilities,
        'reason':'可以读取已同步消息并在本地判断'}]}


class SharedToolsTests(unittest.TestCase):
    def setUp(self):
        self.api=synthetic_api(package())

    def call(self,name,**args):
        return self.api.dispatch('tool',{'name':name,'arguments':args})

    def test_same_definitions_and_real_retrieval_with_citations(self):
        definitions=self.api.dispatch('tools',{'names':['list_chats','timeline']})
        self.assertEqual(definitions,[t for t in FUNCTION_TOOLS if t['function']['name'] in ('list_chats','timeline')])
        result=self.call('list_chats',chat_ids=[],person_ids=[],offset=0,size=30)
        self.assertEqual(len(result['chats']),3)
        page=self.call('timeline',chat_ids=['careers@chatroom'],person_ids=[],offset=0,size=1)
        self.assertFalse(page['complete'])
        self.assertEqual(page['next_offset'],1)
        self.assertEqual(page['items'][0]['reference'],1)
        next_page=self.call('timeline',chat_ids=['careers@chatroom'],person_ids=[],offset=1,size=1)
        self.assertTrue(next_page['complete'])
        self.assertNotEqual(page['items'][0]['evidence_id'],next_page['items'][0]['evidence_id'])
        self.assertTrue(all(100<=i['timestamp']<=1000 for i in self.api.sources))

    def test_native_model_tool_result_round_trip(self):
        first=decision('list_chats')['choices'][0]
        second={'finish_reason':'stop','message':{'role':'assistant','content':'Finished'}}
        self.api.backends['llm']=Mock(side_effect=[(first,{'total_tokens':4}),(second,{'total_tokens':5})])
        messages=[{'role':'user','content':'Find my chats'}]
        response=self.api.dispatch('llm',{'messages':messages,'tools':['list_chats']})
        message=response['message']; call=message['tool_calls'][0]
        result=self.api.dispatch('tool',{'name':call['function']['name'],'arguments':json.loads(call['function']['arguments'])})
        messages += [message,{'role':'tool','tool_call_id':call['id'],'content':json.dumps(result)}]
        last=self.api.dispatch('llm',{'messages':messages,'tools':['list_chats']})
        self.assertEqual(last['message']['content'],'Finished')
        self.assertEqual(self.api.usage['total_tokens'],9)
        self.assertEqual(self.api.backends['llm'].call_args.kwargs['tools'],self.api.api_tools(['list_chats']))

    def test_unknown_tools_permissions_and_history_rejected_before_api(self):
        self.api.backends['llm']=Mock()
        for names in (['send_wechat'],['background'],['timeline','timeline']):
            with self.subTest(names=names),self.assertRaises(ValueError): self.api.api_tools(names)
        with self.assertRaises(ValueError): self.call('timeline',chat_ids=['invented'],person_ids=[],offset=0,size=1)
        with self.assertRaises(ValueError): self.call('timeline',chat_ids=[],person_ids=[],offset=0,size=1,since=0)
        pending=decision('timeline')['choices'][0]['message']; pending['role']='assistant'
        with self.assertRaisesRegex(ValueError,'尚未回传'):
            self.api.dispatch('llm',{'messages':[pending],'tools':['timeline']})
        self.api.limits['permissions']=['llm']
        with self.assertRaises(ValueError): self.api.dispatch('llm',{'messages':[{'role':'user','content':'x'}],'tools':['timeline']})
        self.api.backends['llm'].assert_not_called()

    def test_tool_scope_and_reference_state_survives_receipt_restore(self):
        self.call('list_chats',chat_ids=[],person_ids=[],offset=0,size=20)
        self.call('timeline',chat_ids=['alice'],person_ids=[],offset=0,size=10)
        restored=synthetic_api(package()); restored.restore(copy.deepcopy(self.api.snapshot()))
        data=restored.dispatch('tool',{'name':'timeline','arguments':{'chat_ids':['alice'],'person_ids':['alice'],'offset':0,'size':10}})
        self.assertEqual(data['items'][0]['reference'],1)
        self.assertEqual(len(restored.sources),1)
        with self.assertRaises(ValueError): restored.api_tool('timeline',{'chat_ids':[],'person_ids':['stranger'],'offset':0,'size':10})

    def test_statistics_are_trusted_summaries_not_raw_chat_rows(self):
        data=self.call('statistics',chat_ids=[],person_ids=[])
        self.assertEqual(data['items'][0]['evidence_role'],'statistics')
        self.assertEqual(data['items'][0]['reference'],1)
        with self.assertRaises(ValueError): self.api.api_cite([data['items'][0]])

    def test_search_budget_is_enforced_and_legacy_llm_still_returns_text(self):
        self.api.limits['max_embedding_calls']=0
        backend=Mock(); self.api.tool_backend=backend
        with self.assertRaisesRegex(ValueError,'预算'):
            self.call('search',chat_ids=[],person_ids=[],query='internship',size=20)
        backend.assert_not_called()
        self.api.model_calls=0
        self.assertEqual(self.api.api_llm([{'role':'user','content':'hello'}]),'模拟结果')


class CapabilityTests(unittest.TestCase):
    def test_assessment_receives_actual_runtime_and_api_contract(self):
        complete=Mock(return_value=completion(report()))
        assess_capabilities('检查私聊',complete)
        request=json.loads(complete.call_args.args[0][1]['content'])
        self.assertIn('runtime.context',request['capabilities'])
        self.assertIn('package.config',request['capabilities'])
        self.assertIn('without a top-K cap',request['api_contract'])
        self.assertIn('tool_call_id',request['api_contract'])
        self.assertIn('account (self sender ID)',request['api_contract'])
        schema=complete.call_args.args[1]['json_schema']['schema']
        self.assertEqual(set(schema['properties']['requirements']['items']['properties']['capabilities']['items']['enum']),set(capability_catalog()))

    def test_catalog_generated_from_shared_tools(self):
        catalog=capability_catalog()
        self.assertIn('tool.shared_windows',catalog)
        self.assertNotIn('tool.background',catalog)
        self.assertNotIn('send_wechat',catalog)

    def test_unknown_or_disabled_capability_cannot_be_claimed_supported(self):
        for ids,availability in [(['invented'],{}),(['embedding'],{'embedding':'未配置'})]:
            result,_=assess_capabilities('任务',Mock(return_value=completion(report(capabilities=ids))),availability)
            self.assertEqual(result['status'],'unsupported')
        data=report(); data['requirements'].append({'need':'自动发送','status':'unsupported','capabilities':[],'reason':'只读平台不能发送'})
        result,_=assess_capabilities('任务',Mock(return_value=completion(data)))
        self.assertEqual(result['status'],'partial')

    def test_gap_stops_generation_without_overwriting_or_publishing(self):
        for status in ('partial','unsupported'):
            complete=Mock(return_value=completion(report(status)))
            store=Mock()
            result=generate_code_skill('检查并发送',complete,store,'old','fixture',Mock())
            self.assertNotIn('skill',result)
            self.assertEqual(result['capability_report']['status'],status)
            self.assertEqual(complete.call_count,1)
            store.save_skill_draft.assert_not_called()

    def test_supported_report_is_persisted_with_tested_draft(self):
        complete=Mock(side_effect=[completion(report()),completion(package())])
        with tempfile.TemporaryDirectory() as directory:
            store=GoalStore(Path(directory)/'goals.db')
            with patch('wechat_agent.code_skill_service.validate_code_skill',return_value={'passed':True}):
                result=generate_code_skill('检查私聊',complete,store,'','fixture',Mock())
            self.assertTrue(result['validation']['passed'])
            self.assertEqual(store.list(),[])
            saved=store.skill_drafts()[0]['skill']
            assessment=json.loads(next(f['content'] for f in saved['files'] if f['path']=='CAPABILITIES.json'))
            self.assertEqual(assessment['task'],'检查私聊')
            self.assertEqual(assessment['status'],'supported')

    def test_refusal_and_timeout_do_not_become_no_findings(self):
        for response in ({'choices':[]},completion({'summary':'none','requirements':[]})):
            with self.assertRaises(ValueError): assess_capabilities('任务',Mock(return_value=response))
        with self.assertRaises(TimeoutError): assess_capabilities('任务',Mock(side_effect=TimeoutError('read timeout')))


@unittest.skipUnless(os.environ.get('TEST_CODE_SKILL_SANDBOX')=='1','requires macOS sandbox')
class NativeSkillRecoveryTests(unittest.TestCase):
    def test_sandbox_rpc_and_native_receipts_resume_without_repaying(self):
        code='''import json
def run(api):
    messages=[{'role':'user','content':'Read messages'}]
    reply=api.llm(messages, tools=['timeline'])
    call=reply['message']['tool_calls'][0]
    result=api.tool(call['function']['name'], json.loads(call['function']['arguments']))
    api.log('after retrieval')
    messages += [reply['message'], {'role':'tool','tool_call_id':call['id'],'content':json.dumps(result)}]
    reply=api.llm(messages, tools=['timeline'])
    return json.loads(reply['message']['content'])
'''
        p=package(code)
        first=decision('timeline')['choices'][0]
        final={'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(
            {'summary':'Done','items':[{'title':'Message','detail':'Read','suggestion':None,'source_refs':[1]}],'notice':None})}}
        with tempfile.TemporaryDirectory() as directory:
            goal={'skill':p,'run_id':'native','checkpoint_path':str(Path(directory)/'checkpoint.db'),'profile_signature':'fixture'}
            api=synthetic_api(p); api.backends['llm']=Mock(return_value=(first,{'total_tokens':4}))
            def interrupt(data):
                if data['progress']=='after retrieval': raise RuntimeError('interrupt')
            with self.assertRaisesRegex(Exception,'interrupt'): run_code_skill(goal,api,interrupt,lambda:False,'fixture')
            self.assertEqual(api.backends['llm'].call_count,1)
            resumed=synthetic_api(p); resumed.backends['llm']=Mock(return_value=(final,{'total_tokens':5}))
            goal['resume']=True
            value=run_code_skill(goal,resumed,lambda *a:None,lambda:False,'fixture')
            self.assertEqual(json.loads(value)['summary'],'Done')
            self.assertEqual(resumed.backends['llm'].call_count,1)
            self.assertTrue(resumed.tool_sources)
            self.assertEqual(resumed.usage['total_tokens'],9)


if __name__=='__main__': unittest.main()
