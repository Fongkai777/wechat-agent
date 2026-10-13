import copy
import io
import json
import unittest
from unittest.mock import Mock, patch

from wechat_agent import web
from wechat_agent.platform_tools import FUNCTION_FIELDS, FUNCTION_TOOLS, parse_tool_call
from wechat_agent.platform_tools import FILTER_TOOLS
from wechat_agent.qa_agent import run_conversation_agent
from tests.test_qa_agent import NOW, PEOPLE, completion, decision


class NativeToolTests(unittest.TestCase):
    def test_schemas_have_only_tool_specific_required_parameters(self):
        for tool in FUNCTION_TOOLS:
            function = tool['function']
            schema = function['parameters']
            self.assertTrue(function['strict'])
            self.assertFalse(schema['additionalProperties'])
            expected=set(FUNCTION_FIELDS[function['name']]) | ({'filters'} if function['name'] in FILTER_TOOLS else set())
            self.assertEqual(set(schema['required']),expected)
            self.assertEqual(set(schema['properties']),set(schema['required']))
        finish = next(t['function'] for t in FUNCTION_TOOLS if t['function']['name']=='finish')
        self.assertEqual(finish['parameters']['required'],['source_ids'])

    def test_real_request_uses_tools_not_response_format(self):
        with patch.object(web.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(decision('finish')).encode())) as send:
            web.call_chat_payload({'model':'fixture','base_url':'https://example.test'},'fixture',[],
                                  tools=FUNCTION_TOOLS,tool_choice='required')
        body = json.loads(send.call_args.args[0].data)
        self.assertEqual(body['tools'],FUNCTION_TOOLS)
        self.assertEqual(body['tool_choice'],'required')
        self.assertFalse(body['parallel_tool_calls'])
        self.assertNotIn('response_format',body)

    def test_bad_calls_never_become_local_actions(self):
        base = decision('timeline')
        bad = [None, [], {'choices':'invalid'}, {'choices':[None]},
               {'choices':[{'finish_reason':'tool_calls','message':'invalid'}]}]
        for reason in ('stop','length','content_filter'):
            value = copy.deepcopy(base); value['choices'][0]['finish_reason']=reason; bad.append(value)
        value = copy.deepcopy(base); value['choices'][0]['message']['refusal']='no'; bad.append(value)
        value = copy.deepcopy(base); value['choices'][0]['message']['tool_calls']*=2; bad.append(value)
        for field,new in (('id',''),('type','unknown')):
            value=copy.deepcopy(base); value['choices'][0]['message']['tool_calls'][0][field]=new; bad.append(value)
        for field,new in (('name','shell'),('arguments','{'),('arguments','[]'),('arguments','{"sql":"SELECT *"}')):
            value=copy.deepcopy(base); value['choices'][0]['message']['tool_calls'][0]['function'][field]=new; bad.append(value)
        value=copy.deepcopy(base); value['choices'][0]['message']['tool_calls'][0]['function']='invalid'; bad.append(value)
        value=copy.deepcopy(base); value['choices'][0]['message']['tool_calls'][0]['function']['name']=[]; bad.append(value)
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_tool_call(payload,[],[],{'people':PEOPLE},[])

    def test_native_calls_preserve_scope_and_reject_reused_id(self):
        with self.assertRaisesRegex(ValueError,'已返回的会话ID'):
            parse_tool_call(decision('timeline',chat_ids=['invented']),[],[],{'people':PEOPLE},[])
        with self.assertRaisesRegex(ValueError,'未验证的人物'):
            parse_tool_call(decision('timeline',person_ids=['stranger']),[],[],{'people':PEOPLE},[])
        payload=decision('timeline')
        _,message=parse_tool_call(payload,[],[],{'people':PEOPLE},[])
        with self.assertRaisesRegex(ValueError,'重复使用'):
            parse_tool_call(payload,[],[],{'people':PEOPLE},[message])

    def test_tool_result_is_paired_with_call_before_next_decision(self):
        call=decision('timeline')
        call_id=call['choices'][0]['message']['tool_calls'][0]['id']
        api=Mock(side_effect=[call,decision('finish',source_ids=['one'])])
        tools=Mock()
        tools.state.since_ts=None
        tools.timeline.return_value={'tool':'timeline','items':[{'evidence_id':'one','text':'original'}],'complete':True}
        _,sources,diag=run_conversation_agent('小林今天下午说什么',[],PEOPLE,tools,api,
            Mock(return_value={'paragraphs':[]}),Mock(),Mock(),lambda:None,now=NOW)
        selection=api.call_args_list[0]
        self.assertIsNone(selection.args[1])
        self.assertEqual(selection.kwargs,{'tools':[t for t in FUNCTION_TOOLS if t['function']['name']!='background'],'tool_choice':'required'})
        messages=api.call_args_list[1].args[0]
        assistant=next(i for i,m in enumerate(messages) if m.get('tool_calls'))
        self.assertEqual(messages[assistant]['tool_calls'][0]['id'],call_id)
        self.assertEqual(messages[assistant+1]['role'],'tool')
        self.assertEqual(messages[assistant+1]['tool_call_id'],call_id)
        self.assertEqual(json.loads(messages[assistant+1]['content'])['items'][0]['text'],'original')
        self.assertEqual(diag['trace'][0]['tool_call_id'],call_id)
        self.assertEqual(diag['mode'],'原生工具调用检索')
        self.assertEqual(sources[0]['evidence_id'],'one')


if __name__ == '__main__':
    unittest.main()
