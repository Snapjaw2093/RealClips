"""Tests for select_clips.py using a FAKE Claude, so no API key or cost is needed.

Run:  python -m unittest test_select_clips -v
"""

import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import select_clips as sc

# A fake 120-second transcript: one word per 0.5s, a sentence end every 10 words.
WORDS = [
    {"word": f"word{i}" + ("." if i % 10 == 9 else ""), "start": i * 0.5, "end": i * 0.5 + 0.4}
    for i in range(240)
]
TRANSCRIPT = {"duration": 120.0, "words": WORDS}

GOOD = {
    "clips": [
        {"start": 0, "end": 40, "title": "A", "reason": "r1"},
        {"start": 42, "end": 85, "title": "B", "reason": "r2"},
        {"start": 86, "end": 119, "title": "C", "reason": "r3"},
    ]
}


class FakeClient:
    """Pretends to be the Anthropic client; returns canned replies in order."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.messages = self  # so client.messages.create(...) works

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.replies.pop(0)
        return SimpleNamespace(
            stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)]
        )


def pick(client):
    return sc.select_clips(TRANSCRIPT, 3, 30, 60, client=client)


class SelectClipsTests(unittest.TestCase):
    def test_good_reply_with_code_fence(self):
        client = FakeClient("```json\n" + json.dumps(GOOD) + "\n```")
        clips = pick(client)
        self.assertEqual(len(clips), 3)
        self.assertEqual(len(client.calls), 1)
        # Times were snapped onto real word boundaries (multiples of 0.5).
        for c in clips:
            self.assertEqual(c["start"] % 0.5, 0)

    def test_retries_once_when_clip_too_long(self):
        too_long = json.loads(json.dumps(GOOD))
        too_long["clips"][0]["end"] = 100  # 100s, way over the 60s max
        client = FakeClient(json.dumps(too_long), json.dumps(GOOD))
        self.assertEqual(len(pick(client)), 3)
        self.assertEqual(len(client.calls), 2)
        # The retry told Claude what was wrong.
        self.assertIn("problem", client.calls[1]["messages"][-1]["content"])

    def test_gives_up_after_two_bad_answers(self):
        client = FakeClient("not json at all", "still not json")
        with self.assertRaises(sc.ClipSelectionError):
            pick(client)

    def test_rejects_overlap_and_wrong_count(self):
        overlap = {"clips": [dict(c) for c in GOOD["clips"]]}
        overlap["clips"][1]["start"] = 30
        with self.assertRaises(ValueError):
            sc.validate_clips(overlap, WORDS, 120.0, 3, 30, 60)
        with self.assertRaises(ValueError):
            sc.validate_clips({"clips": GOOD["clips"][:2]}, WORDS, 120.0, 3, 30, 60)

    def test_missing_api_key_message(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "dotenv.load_dotenv", return_value=False
        ):
            with self.assertRaises(sc.ClipSelectionError) as ctx:
                sc.get_client()
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))

    def test_load_picks_from_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            ok = os.path.join(d, "ok.json")
            with open(ok, "w") as f:
                json.dump({"clips": [{"start": 0, "end": 40, "title": "A"}]}, f)  # no reason: allowed
            clips = sc.load_picks(ok, TRANSCRIPT, 30, 60)
            self.assertEqual(clips[0]["reason"], "")

            bad = os.path.join(d, "bad.json")
            with open(bad, "w") as f:
                json.dump({"clips": [{"start": 0, "end": 10, "title": "Too short"}]}, f)
            with self.assertRaises(sc.ClipSelectionError):
                sc.load_picks(bad, TRANSCRIPT, 30, 60)
            with self.assertRaises(sc.ClipSelectionError):
                sc.load_picks(os.path.join(d, "missing.json"), TRANSCRIPT, 30, 60)

    def test_transcript_format_has_times(self):
        text = sc.format_transcript(WORDS[:12])
        self.assertTrue(text.startswith("[0.00] word0 word1"))


if __name__ == "__main__":
    unittest.main()
