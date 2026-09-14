from __future__ import annotations

import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from machboost.codex_integration import (
    CHATGPT_STATE_SCHEMA,
    CODEX_PROFILE_NAME,
    ChatGPTProfileManager,
    CodexCLIProfileManager,
    codex_model_catalog,
    runnable_model_rows,
    select_model_rows,
)


class CodexModelCatalogTests(unittest.TestCase):
    def test_catalog_preserves_runtime_capabilities(self):
        catalog = codex_model_catalog(
            [
                {
                    "name": "muse-glimmer:30b",
                    "display_name": "Muse Glimmer 30B",
                    "capabilities": ["chat", "vision", "reasoning", "tools"],
                    "context_length": 131_072,
                }
            ]
        )

        model = catalog["models"][0]
        self.assertEqual(model["slug"], "muse-glimmer:30b")
        self.assertEqual(model["input_modalities"], ["text", "image"])
        self.assertTrue(model["supports_parallel_tool_calls"])
        self.assertEqual(model["default_reasoning_level"], "medium")
        self.assertEqual(model["context_window"], 131_072)

    def test_only_downloaded_supported_models_are_discovered(self):
        rows = [
            {"name": "ready", "cached": True, "support": "ready"},
            {"name": "download-first", "cached": False, "support": "ready"},
            {"name": "broken", "cached": True, "support": "unsupported"},
            {"id": "claude-sonnet-5", "cached": True},
        ]

        self.assertEqual([row["name"] for row in runnable_model_rows(rows)], ["ready"])

    def test_explicit_model_can_be_configured_before_catalog_discovery(self):
        rows = select_model_rows([], ["team/custom-model"])

        self.assertEqual(rows[0]["name"], "team/custom-model")
        self.assertIn("tools", rows[0]["capabilities"])


class CodexCLIProfileTests(unittest.TestCase):
    def test_profile_is_isolated_and_restore_recovers_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / ".codex"
            state = root / "state.json"
            profile = home / f"{CODEX_PROFILE_NAME}.config.toml"
            catalog = home / "machboost-models.json"
            profile.parent.mkdir(parents=True)
            profile.write_text("original = true\n", encoding="utf-8")
            catalog.write_text('{"old": true}\n', encoding="utf-8")
            manager = CodexCLIProfileManager(home=home, state_path=state)

            status = manager.configure(
                "http://127.0.0.1:11435",
                "secret",
                [{"name": "local/model", "capabilities": ["chat", "tools"]}],
            )

            parsed = tomllib.loads(profile.read_text(encoding="utf-8"))
            self.assertTrue(status["configured"])
            self.assertEqual(parsed["model_provider"], CODEX_PROFILE_NAME)
            self.assertEqual(
                parsed["model_providers"][CODEX_PROFILE_NAME]["base_url"],
                "http://127.0.0.1:11435/v1",
            )
            manager.restore()
            self.assertEqual(profile.read_text(encoding="utf-8"), "original = true\n")
            self.assertEqual(catalog.read_text(encoding="utf-8"), '{"old": true}\n')


class ChatGPTProfileTests(unittest.TestCase):
    def test_configure_and_restore_change_only_managed_root_values(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "machboost.codex_integration.platform.system", return_value="Darwin"
        ):
            root = Path(directory)
            home = root / ".codex"
            state = root / "state.json"
            relay_state = root / "relay.json"
            config = home / "config.toml"
            config.parent.mkdir(parents=True)
            config.write_text(
                'model = "openai-model"\nnotify = ["say", "done"]\n\n[features]\napps = true\n',
                encoding="utf-8",
            )
            manager = ChatGPTProfileManager(
                home=home,
                state_path=state,
                relay_state_path=relay_state,
            )

            connected = manager.configure(
                "http://127.0.0.1:11435",
                [{"name": "local/model", "capabilities": ["chat", "tools"]}],
            )

            parsed = tomllib.loads(config.read_text(encoding="utf-8"))
            self.assertTrue(connected["connected"])
            self.assertEqual(parsed["model"], "local/model")
            self.assertEqual(parsed["openai_base_url"], "http://127.0.0.1:11435/v1")
            self.assertEqual(parsed["notify"], ["say", "done"])
            self.assertTrue(parsed["features"]["apps"])
            self.assertEqual(json.loads(state.read_text())["schema"], CHATGPT_STATE_SCHEMA)

            restored = manager.restore()

            parsed = tomllib.loads(config.read_text(encoding="utf-8"))
            self.assertFalse(restored["connected"])
            self.assertEqual(parsed["model"], "openai-model")
            self.assertNotIn("openai_base_url", parsed)
            self.assertEqual(parsed["notify"], ["say", "done"])
            self.assertTrue(parsed["features"]["apps"])

    def test_existing_profile_and_provider_are_restored(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "machboost.codex_integration.platform.system", return_value="Darwin"
        ):
            root = Path(directory)
            home = root / ".codex"
            config = home / "config.toml"
            config.parent.mkdir(parents=True)
            config.write_text(
                'profile = "work"\nmodel_provider = "proxy"\nmodel = "old"\n',
                encoding="utf-8",
            )
            manager = ChatGPTProfileManager(
                home=home,
                state_path=root / "state.json",
                relay_state_path=root / "relay.json",
            )
            manager.configure(
                "localhost:11435",
                [{"name": "local/model", "capabilities": ["chat"]}],
            )
            manager.restore()

            parsed = tomllib.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(parsed["profile"], "work")
            self.assertEqual(parsed["model_provider"], "proxy")
            self.assertEqual(parsed["model"], "old")


if __name__ == "__main__":
    unittest.main()
