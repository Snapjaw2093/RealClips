"""Tests for the caption-building logic in render.py (no FFmpeg needed).

Run:  python -m unittest test_render -v
"""

import unittest

import render


def w(word, start, end):
    return {"word": word, "start": start, "end": end}


class CaptionTests(unittest.TestCase):
    def test_clean_words_glues_punctuation_pieces(self):
        words = [w("five", 0, 0.3), w("-minute", 0.3, 0.6), w("$685", 1, 1.3), w(",000.", 1.3, 1.6)]
        cleaned = render.clean_words(words)
        self.assertEqual([c["word"] for c in cleaned], ["five-minute", "$685,000."])
        self.assertEqual(cleaned[0]["end"], 0.6)  # merged word spans both pieces

    def test_group_words_by_count_and_sentence_end(self):
        words = [w(t, i, i + 0.5) for i, t in enumerate(["a", "b", "c", "d", "e.", "f"])]
        groups = render.group_words(words, per_caption=3)
        self.assertEqual([[x["word"] for x in g] for g in groups], [["a", "b", "c"], ["d", "e."], ["f"]])

    def test_ass_time_format(self):
        self.assertEqual(render.ass_time(0), "0:00:00.00")
        self.assertEqual(render.ass_time(61.5), "0:01:01.50")
        self.assertEqual(render.ass_time(3725.25), "1:02:05.25")

    def test_build_ass_shifts_times_to_clip_start(self):
        words = [w("outside", 1, 1.4), w("hello", 10, 10.4), w("there.", 10.4, 10.9), w("later", 50, 50.4)]
        ass = render.build_ass(words, clip_start=10, clip_end=20)
        dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue")]
        self.assertEqual(len(dialogue), 1)  # words outside the clip are dropped
        self.assertIn("0:00:00.00,0:00:00.90", dialogue[0])  # time 0 = clip start
        self.assertTrue(dialogue[0].endswith("hello there."))
        self.assertIn("PlayResX: 1080", ass)

    def test_braces_removed_from_caption_text(self):
        ass = render.build_ass([w("{bad}", 0, 1)], 0, 5)
        self.assertNotIn("{bad}", ass)


if __name__ == "__main__":
    unittest.main()
