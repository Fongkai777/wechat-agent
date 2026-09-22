import unittest
import xml.etree.ElementTree as ET

from wechat_agent.web import display_content, parse_app_message, parse_message_xml, readable_message_text


INVITE = '''1234567890@chatroom:
<sysmsg type="sysmsgtemplate"><sysmsgtemplate>
<content_template type="tmpl_type_profile">
<plain><![CDATA[]]></plain>
<template><![CDATA[$username$ invited $names$ to the group chat]]></template>
<link_list>
<link name="username" type="link_profile"><memberlist><member>
<username><![CDATA[wxid_inviter]]></username><nickname><![CDATA[Alice]]></nickname>
</member></memberlist></link>
<link name="names" type="link_profile"><memberlist><member>
<username><![CDATA[wxid_invitee]]></username><nickname><![CDATA[Bob]]></nickname>
</member></memberlist><separator><![CDATA[, ]]></separator></link>
</link_list></content_template></sysmsgtemplate></sysmsg>'''


class SystemMessageTests(unittest.TestCase):
    def test_invitation_removes_room_prefix_and_resolves_names(self):
        self.assertEqual(display_content(INVITE, "system"), "Alice 邀请 Bob 加入了群聊")

    def test_multiple_members_preserve_separator(self):
        root = ET.fromstring(INVITE.split("\n", 1)[1])
        members = root.find(".//link[@name='names']/memberlist")
        member = ET.SubElement(members, "member")
        ET.SubElement(member, "nickname").text = "Another"
        self.assertEqual(display_content(ET.tostring(root, encoding="unicode"), "system"), "Alice 邀请 Bob, Another 加入了群聊")

    def test_plain_text_has_priority(self):
        content = INVITE.replace("<plain><![CDATA[]]></plain>", "<plain><![CDATA[已经加入群聊]]></plain>")
        self.assertEqual(display_content(content, "system"), "已经加入群聊")

    def test_missing_nickname_falls_back_to_username(self):
        content = INVITE.replace("<nickname><![CDATA[Bob]]></nickname>", "")
        self.assertEqual(display_content(content, "system"), "Alice 邀请 wxid_invitee 加入了群聊")

    def test_unknown_template_resolves_without_inventing_event(self):
        content = INVITE.replace("$username$ invited $names$ to the group chat", "$username$ changed the group name")
        self.assertEqual(display_content(content, "system"), "Alice changed the group name")

    def test_nickname_is_not_interpreted_as_another_placeholder(self):
        content = INVITE.replace("CDATA[Alice]", "CDATA[$names$]")
        self.assertEqual(display_content(content, "system"), "$names$ 邀请 Bob 加入了群聊")

    def test_user_text_and_readable_system_notices_are_preserved(self):
        self.assertEqual(display_content(INVITE, "text"), INVITE)
        self.assertEqual(display_content("你已加入群聊", "system"), "你已加入群聊")

    def test_missing_members_and_malformed_xml_have_safe_fallbacks(self):
        self.assertEqual(display_content("<sysmsg>broken", "system"), "[系统消息]")
        content = '<sysmsg><sysmsgtemplate><content_template><template>$user$ joined</template></content_template></sysmsgtemplate></sysmsg>'
        self.assertEqual(display_content(content, "system"), "某位成员 joined")

    def test_revocation_supports_both_content_fields(self):
        for field in ("content", "replacemsg"):
            xml = f'<sysmsg type="revokemsg"><revokemsg><{field}>A 撤回了一条消息</{field}></revokemsg></sysmsg>'
            self.assertEqual(display_content(xml, "system"), "A 撤回了一条消息")

    def test_system_rich_text_keeps_visible_words_not_links(self):
        text = '你已支付 <_wc_custom_link_ href="weixin://private">查看详情</_wc_custom_link_>'
        self.assertEqual(display_content(text, "system"), "你已支付 查看详情")
        xml = f'<sysmsg type="paymsg"><content><![CDATA[{text}]]></content></sysmsg>'
        self.assertEqual(display_content(xml, "system"), "你已支付 查看详情")

    def test_group_management_messages(self):
        removed = '<sysmsg type="delchatroommember"><delchatroommember><plain>你已被移出群聊</plain></delchatroommember></sysmsg>'
        self.assertEqual(display_content(removed, "system"), "你已被移出群聊")
        pinned = '<sysmsg type="mmchatroomtopmsg"><mmchatroomtopmsg><nickname>A</nickname></mmchatroomtopmsg></sysmsg>'
        self.assertEqual(display_content(pinned, "system"), "A 更新了群置顶消息")

    def test_location_contact_card_and_email(self):
        location = '<msg><location poiname="Library" label="1 Main Road" x="1.2" y="103.4" /></msg>'
        self.assertEqual(display_content(location, "type_48"), "[位置]\nLibrary\n1 Main Road")
        card = '<msg nickname="Alice" alias="alice123" username="wxid_1" ticket="private" />'
        self.assertEqual(display_content(card, "type_42"), "[联系人名片]\nAlice\nalice123")
        self.assertEqual(display_content(card, "type_66"), "[企业微信名片]\nAlice\nalice123")
        email = '<msg><pushmail><content><subject>Reminder</subject><sender>Alice</sender><digest>Meeting</digest></content></pushmail></msg>'
        self.assertEqual(display_content(email, "type_35"), "[邮件通知]\nReminder\nAlice\nMeeting")

    def test_null_terminated_app_message_is_recovered(self):
        xml = '<msg><appmsg><type>5</type><title>Article</title></appmsg></msg>\x00'
        self.assertEqual(parse_app_message(xml)["title"], "Article")

    def test_misplaced_declaration_and_sibling_voip_metadata(self):
        xml = '<msg><?xml version="1.0"?><appmsg><type>5</type><title>Article</title></appmsg></msg>'
        self.assertEqual(parse_app_message(xml)["title"], "Article")
        voip = '<voipmsg><VoIPBubbleMsg><msg>Declined</msg></VoIPBubbleMsg></voipmsg><voipinvitemsg><roomid>1</roomid></voipinvitemsg>'
        self.assertEqual(display_content(voip, "voip"), "[音视频通话] 已拒绝")

    def test_embedded_cdata_declaration_is_not_taken_as_root(self):
        xml = '<msg><appmsg><type>19</type><title>History</title><recorditem><![CDATA[<?xml version="1.0"?><recordinfo/>]]></recorditem></appmsg></msg>'
        self.assertEqual(parse_message_xml(xml).tag, "msg")
        self.assertIn('<?xml version="1.0"?>', parse_message_xml(xml).findtext("appmsg/recorditem"))

    def test_app_rich_text_and_invalid_xml_fallback(self):
        xml = '<msg><appmsg><type>5</type><title><![CDATA[<a href="https://example.com">Title</a>]]></title><des><![CDATA[<p>One</p><p>Two</p>]]></des></appmsg></msg>'
        app = parse_app_message(xml)
        self.assertEqual((app["title"], app["description"]), ("Title", "One\nTwo"))
        self.assertEqual(display_content('<msg><appmsg>broken', "app"), "[应用消息 · 内容暂无法解析]")
        self.assertEqual(display_content('<msg><secret>data</secret></msg>', "type_999"), "[暂不支持的消息 · type_999]")
        self.assertEqual(readable_message_text("Size <L>"), "Size <L>")
        self.assertEqual(readable_message_text('<script>unsafe()</script><p>Visible</p>'), "Visible")


if __name__ == "__main__":
    unittest.main()
