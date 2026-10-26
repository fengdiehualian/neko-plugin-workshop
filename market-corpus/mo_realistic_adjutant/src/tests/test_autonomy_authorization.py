from __future__ import annotations

import unittest

from mo_companion.autonomy_authorization import (
    classify_autonomy_authorization,
    extract_user_text,
)


class NaturalAutonomyAuthorizationTests(unittest.TestCase):
    def test_exact_reported_sentence_authorizes(self):
        result = classify_autonomy_authorization(
            "小pan，接下来我将授权给你完全自主作战击败对手"
        )
        self.assertTrue(result["authorized"])

    def test_natural_variants_authorize(self):
        samples = (
            "这局交给你全权操作，自己发展并击败对面",
            "不用再问我，你来自主作战直到胜利",
            "请接管本局，自动打完并战胜对手",
            "允许你独立作战，拿下这局",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(classify_autonomy_authorization(sample)["authorized"])

    def test_questions_and_negations_do_not_authorize(self):
        samples = (
            "你能不能完全自主作战击败对手？",
            "不要自主作战，先等我的命令",
            "取消全权操作",
            "你是否支持自主对战？",
            "我没有授权你自主击败对手",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertFalse(classify_autonomy_authorization(sample)["authorized"])

    def test_unrelated_authorization_does_not_start_game(self):
        self.assertFalse(
            classify_autonomy_authorization("授权你完全自主整理这些文件")["authorized"]
        )

    def test_extracts_sdk_and_memory_shapes(self):
        expected = "授权你完全自主作战击败对手"
        self.assertEqual(extract_user_text({"role": "user", "content": expected}), expected)
        self.assertEqual(
            extract_user_text({"payload": {"type": "user_message", "text": expected}}),
            expected,
        )


if __name__ == "__main__":
    unittest.main()
