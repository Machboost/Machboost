import os
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from machboost.providers import (
    ProviderBudgetError,
    ProviderConfig,
    ProviderCancelled,
    ProviderError,
    ProviderStore,
    _default_transport,
    _default_stream_transport,
    route_with_fallback,
)


class ProviderStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.calls = []

        def transport(config, path, payload, headers):
            self.calls.append((config, path, payload, headers))
            return {
                "id": "chatcmpl_test",
                "choices": [{"message": {"role": "assistant", "content": "hello"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 25},
            }

        self.store = ProviderStore(
            Path(self.temporary.name) / "team.sqlite3",
            transport=transport,
            clock=lambda: 1_725_000_000.0,
        )

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def configure(self, **overrides):
        values = {
            "provider_id": "primary",
            "name": "Primary",
            "base_url": "https://inference.example.com",
            "models": ["qwen-coder", "fallback-model"],
            "api_key": "top-secret",
            "monthly_budget_usd": 10.0,
            "input_cost_per_million": 2.0,
            "output_cost_per_million": 8.0,
        }
        values.update(overrides)
        return self.store.configure(**values)

    def test_configuration_never_persists_or_returns_secret(self):
        config = self.configure()
        raw = (Path(self.temporary.name) / "team.sqlite3").read_bytes()

        self.assertNotIn(b"top-secret", raw)
        listed = self.store.list()[0]
        self.assertTrue(listed["has_secret"])
        self.assertNotIn("api_key", listed)
        self.assertEqual(config.models, ("qwen-coder", "fallback-model"))

    def test_remote_http_is_rejected_but_loopback_is_allowed(self):
        with self.assertRaises(ValueError):
            self.configure(base_url="http://inference.example.com")
        config = self.configure(base_url="http://127.0.0.1:8000")
        self.assertEqual(config.base_url, "http://127.0.0.1:8000")

    def test_chat_forwards_auth_and_records_cost(self):
        self.configure()
        result = self.store.chat(
            {"model": "qwen-coder", "messages": [{"role": "user", "content": "hi"}]}
        )

        self.assertEqual(result.response["choices"][0]["message"]["content"], "hello")
        self.assertEqual(result.prompt_tokens, 100)
        self.assertAlmostEqual(result.cost_usd, 0.0004)
        self.assertEqual(self.calls[0][1], "/chat/completions")
        self.assertEqual(self.calls[0][3]["Authorization"], "Bearer top-secret")
        usage = self.store.usage()["usage"][0]
        self.assertEqual(usage["requests"], 1)
        self.assertEqual(usage["prompt_tokens"], 100)

    def test_environment_secret_is_loaded_without_persistence(self):
        self.configure(api_key=None, api_key_env="MACHBOOST_TEST_PROVIDER_KEY")
        with patch.dict(os.environ, {"MACHBOOST_TEST_PROVIDER_KEY": "from-env"}):
            self.store.chat({"model": "qwen-coder", "messages": []})
        self.assertEqual(self.calls[0][3]["Authorization"], "Bearer from-env")

    def test_unknown_model_and_missing_secret_fail_closed(self):
        self.configure(api_key=None)
        with self.assertRaises(ProviderError) as unknown:
            self.store.chat({"model": "other", "messages": []})
        self.assertEqual(unknown.exception.status, 404)
        with self.assertRaises(ProviderError) as missing:
            self.store.chat({"model": "qwen-coder", "messages": []})
        self.assertEqual(missing.exception.status, 401)

    def test_monthly_budget_blocks_more_requests(self):
        self.configure(monthly_budget_usd=0.0)
        with self.assertRaises(ProviderBudgetError):
            self.store.chat({"model": "qwen-coder", "messages": []})

    def test_delete_removes_metadata_and_secret(self):
        self.configure()
        self.assertTrue(self.store.delete("primary"))
        self.assertFalse(self.store.delete("primary"))
        self.assertEqual(self.store.list(), [])


class ProviderTransportTests(unittest.TestCase):
    def test_root_and_versioned_base_urls_target_one_api_version(self):
        for base, expected in [
            ("https://api.example.com", "https://api.example.com/v1/chat/completions"),
            ("https://api.example.com/", "https://api.example.com/v1/chat/completions"),
            ("https://api.example.com/v1", "https://api.example.com/v1/chat/completions"),
            ("https://api.example.com/v1/", "https://api.example.com/v1/chat/completions"),
            ("https://api.example.com/openai/v1", "https://api.example.com/openai/v1/chat/completions"),
            ("https://api.example.com/gateway", "https://api.example.com/gateway/v1/chat/completions"),
        ]:
            with self.subTest(base=base), patch("machboost.providers.urllib.request.urlopen") as opened:
                response = MagicMock()
                response.read.return_value = b'{"choices": []}'
                opened.return_value.__enter__.return_value = response
                config = ProviderConfig(id="meta", name="Meta", base_url=base, models=("muse-spark-1.3",))
                self.assertEqual(
                    _default_transport(config, "/chat/completions", {"model": "muse-spark-1.3"}, {"Authorization": "Bearer test"}),
                    {"choices": []},
                )
                request = opened.call_args.args[0]
                self.assertEqual(request.full_url, expected)
                self.assertEqual(request.get_header("Authorization"), "Bearer test")


class ProviderStreamingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ProviderStore(Path(self.temporary.name) / "providers.sqlite3")
        self.store.configure(name="One", provider_id="one", base_url="https://example.com/v1",
                             models=["test"], api_key="test-key")

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def packet(self, delta, finish=None):
        return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    def test_stream_emits_before_completion_and_assembles_tool_fragments(self):
        seen = []
        def upstream(config, path, payload, headers, cancel):
            self.assertTrue(payload["stream"])
            self.assertTrue(payload["stream_options"]["include_usage"])
            self.assertEqual(headers["Authorization"], "Bearer test-key")
            yield self.packet({"reasoning_content": "Think "})
            self.assertEqual(len(seen), 1)
            yield self.packet({"reasoning_content": "first", "content": "Hello "})
            yield self.packet({"content": "world", "tool_calls": [
                {"index": 0, "id": "a", "function": {"name": "read", "arguments": '{"path":'}},
                {"index": 1, "id": "b", "function": {"name": "list", "arguments": "{"}},
            ]})
            yield self.packet({"tool_calls": [
                {"index": 0, "function": {"arguments": '"a.py"}'}},
                {"index": 1, "function": {"arguments": "}"}},
            ]}, "tool_calls")
            yield {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 8}}
        self.store.stream_transport = upstream
        result = self.store.stream_chat({"model": "test"}, on_chunk=seen.append)
        message = result.response["choices"][0]["message"]
        self.assertEqual(message["content"], "Hello world")
        self.assertEqual(message["reasoning_content"], "Think first")
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"]), {"path": "a.py"})
        self.assertEqual(json.loads(message["tool_calls"][1]["function"]["arguments"]), {})
        self.assertTrue(result.streamed)
        self.assertIsNotNone(result.time_to_first_token_seconds)
        self.assertEqual(result.stats["generated_tokens"], 8)
        self.assertEqual(self.store.usage()["usage"][0]["requests"], 1)

    def test_failure_after_a_delta_never_retries_another_provider(self):
        self.store.configure(name="Two", provider_id="two", base_url="https://other.example.com",
                             models=["test"], api_key="another-key")
        calls = []
        def upstream(config, *args):
            calls.append(config.id)
            yield self.packet({"content": "partial"})
            raise ProviderError("disconnected", transient=True)
        self.store.stream_transport = upstream
        seen = []
        with self.assertRaises(ProviderError):
            self.store.stream_chat({"model": "test"}, on_chunk=seen.append)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(seen), 1)

    def test_transient_failure_before_a_delta_can_retry(self):
        self.store.configure(name="Two", provider_id="two", base_url="https://other.example.com",
                             models=["test"], api_key="another-key")
        calls = []
        def upstream(config, *args):
            calls.append(config.id)
            if len(calls) == 1:
                raise ProviderError("busy", status=503, transient=True)
            yield self.packet({"content": "ok"}, "stop")
        self.store.stream_transport = upstream
        result = self.store.stream_chat({"model": "test"}, on_chunk=lambda packet: None)
        self.assertEqual(result.text, "ok")
        self.assertEqual(len(calls), 2)

    def test_cancellation_closes_generator_without_replaying(self):
        cancelled = threading.Event()
        closed = threading.Event()
        def upstream(*args):
            try:
                yield self.packet({"content": "one"})
                yield self.packet({"content": "two"}, "stop")
            finally:
                closed.set()
        self.store.stream_transport = upstream
        with self.assertRaises(ProviderCancelled):
            self.store.stream_chat({"model": "test"}, on_chunk=lambda packet: cancelled.set(),
                                   cancel_event=cancelled)
        self.assertTrue(closed.is_set())

    def test_truncated_stream_is_not_reported_as_success(self):
        self.store.stream_transport = lambda *args: iter([self.packet({"content": "partial"})])
        with self.assertRaisesRegex(ProviderError, "completion marker"):
            self.store.stream_chat({"model": "test"}, on_chunk=lambda packet: None)
        self.assertEqual(self.store.usage()["usage"], [])

    def test_sse_parser_handles_comments_multiline_unicode_and_done(self):
        wire = '\ufeff: heartbeat\r\ndata: {"choices": [],\r\ndata: "label": "caf\u00e9"}\r\n\r\ndata: [DONE]\n\n'
        config = self.store.candidates("test", "one")[0]
        with patch("machboost.providers.urllib.request.urlopen") as opened:
            opened.return_value.__enter__.return_value = io.BytesIO(wire.encode("utf-8"))
            events = list(_default_stream_transport(config, "/chat/completions", {}, {}, None))
        self.assertEqual(events, [{"choices": [], "label": "caf\u00e9"}])

    def test_sse_parser_rejects_malformed_json(self):
        config = self.store.candidates("test", "one")[0]
        with patch("machboost.providers.urllib.request.urlopen") as opened:
            opened.return_value.__enter__.return_value = io.BytesIO(b"data: not-json\n\n")
            with self.assertRaisesRegex(ProviderError, "invalid SSE JSON"):
                list(_default_stream_transport(config, "/chat/completions", {}, {}, None))

    def test_usage_option_is_removed_only_when_explicitly_rejected(self):
        config = self.store.candidates("test", "one")[0]
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(b'data: {"choices":[]}\n\ndata: [DONE]\n\n')
        rejected = HTTPError("https://example.com", 400, "Bad Request", {},
                             io.BytesIO(b'{"error":{"param":"stream_options"}}'))
        with patch("machboost.providers.urllib.request.urlopen", side_effect=[rejected, response]) as opened:
            list(_default_stream_transport(config, "/chat/completions",
                                           {"stream": True, "stream_options": {"include_usage": True}}, {}, None))
            self.assertEqual(opened.call_count, 2)
            self.assertNotIn("stream_options", json.loads(opened.call_args.args[0].data))
        rejected = HTTPError("https://example.com", 401, "Unauthorized", {}, io.BytesIO(b'{}'))
        with patch("machboost.providers.urllib.request.urlopen", side_effect=rejected) as opened:
            with self.assertRaises(ProviderError):
                list(_default_stream_transport(config, "/chat/completions", {"stream_options": {}}, {}, None))
            self.assertEqual(opened.call_count, 1)

    def test_real_http_stream_cancels_a_blocked_read(self):
        release = threading.Event()
        first = threading.Event()
        cancelled = threading.Event()
        failures = []
        class Upstream(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n')
                self.wfile.flush()
                release.wait(5)

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.store.configure(name="HTTP", provider_id="http", base_url=f"http://127.0.0.1:{server.server_port}/v1",
                             models=["test"], api_key="test-key")
        def read():
            try:
                self.store.stream_chat({"model": "test"}, provider_id="http",
                                       on_chunk=lambda packet: first.set(), cancel_event=cancelled)
            except Exception as exc:
                failures.append(exc)
        reader = threading.Thread(target=read, daemon=True)
        try:
            reader.start()
            self.assertTrue(first.wait(3), "first SSE event was buffered")
            cancelled.set()
            reader.join(2)
            self.assertFalse(reader.is_alive(), "cancellation did not interrupt a blocked socket read")
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], ProviderCancelled)
        finally:
            cancelled.set()
            release.set()
            reader.join(3)
            server.shutdown()
            server.server_close()
            thread.join(2)


class RouteFallbackTests(unittest.TestCase):
    def test_modes_choose_expected_primary(self):
        self.assertEqual(
            route_with_fallback("local_only", local=lambda: "local", external=lambda: "remote"),
            ("local", "local"),
        )
        self.assertEqual(
            route_with_fallback(
                "external_first", local=lambda: "local", external=lambda: "remote"
            ),
            ("external", "remote"),
        )

    def test_transient_error_falls_back(self):
        source, value = route_with_fallback(
            "external_first",
            local=lambda: "local",
            external=lambda: (_ for _ in ()).throw(
                ProviderError("rate limited", status=429, transient=True)
            ),
        )
        self.assertEqual((source, value), ("local", "local"))

    def test_auth_and_budget_errors_do_not_fall_back(self):
        called = []
        with self.assertRaises(ProviderError):
            route_with_fallback(
                "external_first",
                local=lambda: called.append(True),
                external=lambda: (_ for _ in ()).throw(
                    ProviderError("unauthorized", status=401, transient=False)
                ),
            )
        self.assertEqual(called, [])

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            route_with_fallback("random", local=lambda: None, external=lambda: None)


if __name__ == "__main__":
    unittest.main()
