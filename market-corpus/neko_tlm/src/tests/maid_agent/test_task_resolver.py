import importlib
import unittest

from ._bootstrap import bootstrap_sdk

bootstrap_sdk()

task_resolver = importlib.import_module("neko_tlm.task_resolver")


class TaskResolverTests(unittest.TestCase):
    def setUp(self):
        self.available = [
            {"id": "touhou_little_maid:idle", "name": "待机"},
            {"id": "touhou_little_maid:board_games", "name": "游戏"},
        ]

    def test_chess_phrases_resolve_to_tlm_game_mode(self):
        for phrase in ("下棋", "五子棋", "过来下棋", "来玩游戏", "游戏模式"):
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    "touhou_little_maid:board_games",
                    task_resolver.resolve_task_name(phrase, self.available),
                )

    def test_chess_resolves_without_dynamic_task_list(self):
        self.assertEqual(
            "touhou_little_maid:board_games",
            task_resolver.resolve_task_name("下棋"),
        )


if __name__ == "__main__":
    unittest.main()
