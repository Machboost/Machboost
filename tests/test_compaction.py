import unittest

from machboost.compaction import envelope, expand_items, prepare, triggered
from machboost.protocols import responses_messages


class CompactionTests(unittest.TestCase):
    def test_round_trip_retains_tool_pairs_and_instructions(self):
        items = [{"role": "system", "content": "Stay in workspace"},
                 {"type": "function_call", "call_id": "a", "name": "read", "arguments": "{}"},
                 {"type": "function_call_output", "call_id": "a", "output": "source"}]
        items += [{"role": "user", "content": str(i)} for i in range(10)]
        older, retained = prepare({"input": items}, stream=False)
        self.assertEqual(retained[:3], items[:3])
        self.assertEqual(len(older), 6)
        state = envelope("Prior work", retained)
        restored = expand_items([state, {"role": "user", "content": "Continue"}])
        self.assertEqual(restored[1:-1], retained)
        self.assertIn("Prior work", responses_messages({"input": [state]})[0]["content"])

    def test_foreign_or_empty_envelope_rejected(self):
        for value in ["garbage", "{}", "null"]:
            with self.assertRaises(ValueError):
                expand_items([{"type": "compaction", "encrypted_content": value}])
        with self.assertRaises(ValueError):
            envelope(" ", [])

    def test_trigger_validation(self):
        trigger = {"type": "compaction_trigger"}
        self.assertTrue(triggered({"input": [trigger], "stream": True}))
        for items, stream in [([trigger], False), ([trigger, {}], True), ([trigger, trigger], True)]:
            with self.assertRaises(ValueError):
                triggered({"input": items, "stream": stream})

    def test_images_and_pending_tools_retained(self):
        image = {"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,AA=="}]}
        pending = {"type": "function_call", "call_id": "pending", "name": "test", "arguments": "{}"}
        _, retained = prepare({"input": [image, pending] + [{"role": "user", "content": "hi"}] * 8}, stream=False)
        self.assertEqual(retained[:2], [image, pending])
