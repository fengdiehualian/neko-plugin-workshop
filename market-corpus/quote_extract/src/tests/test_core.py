"""core.py 单元测试：PROPFIND 解析、分池概率、不重复抽取。"""

import random

from core import choose_pool, is_image, mime_for, parse_propfind, pick

PROPFIND_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response><D:href>/dav/memes/</D:href></D:response>
  <D:response><D:href>/dav/memes/a.png</D:href></D:response>
  <D:response><D:href>/dav/memes/b%20c.JPG</D:href></D:response>
  <D:response><D:href>/dav/memes/note.txt</D:href></D:response>
</D:multistatus>
"""


class TestParsePropfind:
    def test_hrefs_extracted(self):
        hrefs = parse_propfind(PROPFIND_SAMPLE)
        assert "/dav/memes/" in hrefs
        assert "/dav/memes/a.png" in hrefs

    def test_invalid_xml_returns_empty(self):
        assert parse_propfind(b"<broken") == []


class TestImageFilter:
    def test_is_image(self):
        assert is_image("/dav/memes/a.PNG")
        assert is_image("/dav/memes/b.jpeg")
        assert not is_image("/dav/memes/note.txt")
        assert not is_image("/dav/memes/")

    def test_mime(self):
        assert mime_for("/x/a.png") == "image/png"
        assert mime_for("/x/b.JPG") == "image/jpeg"
        assert mime_for("/x/c.gif") == "image/gif"
        assert mime_for("/x/weird") == "application/octet-stream"


class TestChoosePool:
    def test_hidden_hits_by_rate(self):
        rng = random.Random(42)
        hits = sum(
            choose_pool(["n1"], ["h1"], 0.5, rng)[1] for _ in range(200)
        )
        assert 60 < hits < 140  # 0.5 概率下 200 次的合理区间

    def test_hidden_rate_zero_never_hidden(self):
        for _ in range(50):
            _, is_hidden = choose_pool(["n1"], ["h1"], 0.0, random.Random(1))
            assert not is_hidden

    def test_empty_hidden_falls_back_to_normal(self):
        for _ in range(50):
            pool, is_hidden = choose_pool(["n1"], [], 0.99, random.Random(1))
            assert pool == ["n1"] and not is_hidden

    def test_empty_normal_falls_back_to_hidden(self):
        pool, is_hidden = choose_pool([], ["h1"], 0.0, random.Random(1))
        assert pool == ["h1"] and is_hidden

    def test_both_empty(self):
        assert choose_pool([], [], 0.5, random.Random(1)) == ([], False)


class TestPick:
    def test_no_repeat(self):
        pool = ["a", "b", "c"]
        history: list[str] = []
        first = pick(pool, history, 2, random.Random(1))
        second = pick(pool, history, 2, random.Random(1))
        assert first != second
        assert history == [first, second]

    def test_repeat_allowed_when_disabled(self):
        pool = ["a", "b"]
        history: list[str] = []
        rng = random.Random(1)
        results = {pick(pool, history, 0, rng) for _ in range(20)}
        assert results == {"a", "b"}

    def test_history_capped(self):
        pool = ["a", "b"]
        history: list[str] = []
        for _ in range(300):
            pick(pool, history, 0, random.Random(1))
        assert len(history) == 200

    def test_empty_pool(self):
        assert pick([], [], 5, random.Random(1)) is None
