import sys
import unittest
from types import SimpleNamespace

from machboost import Accelerator
from machboost.generation import output_token_limit
from tests.test_accelerator import ScriptedService


class OutputLimitTests(unittest.TestCase):
    def limit(self, requested, config=None, tokens=10, tokenizer=None):
        return output_token_limit(
            requested, model=SimpleNamespace(config=config),
            tokenizer=tokenizer, prompt_tokens=tokens,
        )

    def test_uncapped_uses_remaining_model_context(self):
        for config in (
            {"max_position_embeddings": 262144},
            {"text_config": {"max_position_embeddings": 262144}},
            SimpleNamespace(language_config=SimpleNamespace(max_position_embeddings=262144)),
        ):
            self.assertEqual(self.limit(-1, config), 262134)

    def test_unknown_context_does_not_impose_128_token_default(self):
        tokenizer = SimpleNamespace(model_max_length=10**30)
        self.assertEqual(self.limit(-1, tokenizer=tokenizer), sys.maxsize)

    def test_explicit_limits_and_prefill_only_are_preserved(self):
        self.assertEqual(self.limit(64), 64)
        self.assertEqual(self.limit(0), 0)
        with self.assertRaises(ValueError):
            self.limit(-3)

    def test_full_context_is_reported_not_silently_empty(self):
        with self.assertRaisesRegex(ValueError, "context window"):
            self.limit(-1, {"max_position_embeddings": 10})

    def test_uncapped_generation_can_finish_beyond_old_limit(self):
        output = "<think>" + "reason " * 50 + "</think>There is no unique difference."
        accelerator = Accelerator(ScriptedService("question", output), boost_enabled=False)
        text, stats = accelerator.generate("question", max_tokens=-1)
        self.assertEqual(text, output)
        self.assertGreater(stats.generated_tokens, 128)


if __name__ == "__main__":
    unittest.main()
