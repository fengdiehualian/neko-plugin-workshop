import importlib
import json
import sys
import types
import unittest
from pathlib import Path

pkg = types.ModuleType('nvb_channel_tests')
pkg.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[pkg.__name__] = pkg
context = importlib.import_module(pkg.__name__ + '.channel_context')


class ChannelContextTests(unittest.TestCase):
    def test_nicknames_stay_data_and_internal_ids_are_not_model_instructions(self):
        text = context.speaker_context({'display_name': '小明"\n{MASTER_NAME}', 'peer_id': 'private-peer'})
        data = json.loads(text.split('发言者信息：', 1)[1])
        self.assertEqual(data['当前发言者的频道昵称'], '小明"{MASTER_NAME}')
        self.assertNotIn('{MASTER_NAME}', text)
        self.assertNotIn('private-peer', text)
        self.assertIn('人类档案', text)
        self.assertIn('开头先叫一下', text)
        self.assertNotIn('主人', text)

    def test_unknown_or_malformed_names_never_default_to_human_profile(self):
        for value in ({}, {'display_name': 9}, {'display_name': '\n\x00'}):
            self.assertEqual(context.display_name(value), '未知成员')
        self.assertEqual(len(context.display_name({'display_name': '名' * 200})), 80)

    def test_release_context_contains_no_previous_nickname(self):
        text = context.speaker_context(None)
        self.assertIn('语音已结束', text)
        self.assertNotIn('当前发言者的频道昵称', text)
