import unittest

from machboost.reasoning import ThinkingStreamSplitter


class ReasoningStreamTests(unittest.TestCase):
    def test_turn_markers_are_removed_across_every_chunk_boundary(self):
        for marker in ("<turn|>", "<end_turn|>"):
            text = "You're welcome!" + marker
            for boundary in range(len(text) + 1):
                with self.subTest(marker=marker, boundary=boundary):
                    splitter = ThinkingStreamSplitter()
                    deltas = [splitter.feed(text[:boundary]), splitter.feed(text[boundary:]), splitter.feed("", final=True)]
                    self.assertEqual("".join(delta.content for delta in deltas), "You're welcome!")
                    self.assertEqual("".join(delta.reasoning for delta in deltas), "")

    def test_turn_marker_after_reasoning_is_not_visible(self):
        splitter = ThinkingStreamSplitter()
        delta = splitter.feed("<think>Consider this.</think>Answer.<turn|>", final=True)
        self.assertEqual(delta.reasoning, "Consider this.")
        self.assertEqual(delta.content, "Answer.")

    def test_similar_but_non_protocol_text_survives(self):
        splitter = ThinkingStreamSplitter()
        text = "Compare x < y and <turn> elements."
        pieces = [splitter.feed(char).content for char in text]
        pieces.append(splitter.feed("", final=True).content)
        self.assertEqual("".join(pieces), text)


if __name__ == "__main__":
    unittest.main()
