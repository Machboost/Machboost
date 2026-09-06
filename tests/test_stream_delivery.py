from __future__ import annotations

import json
import threading
import unittest
from urllib.request import Request, urlopen

from machboost.server import MachBoostHTTPServer, RuntimeManager
from tests.test_server import FakeAccelerator, FakeMuseStats


class GatedAccelerator(FakeAccelerator):
    def __init__(self, release, *, ordered=False):
        super().__init__()
        self.release = release
        self.ordered = ordered

    def generate_chat(self, messages, *, on_text=None, on_thinking=None, **kwargs):
        if self.ordered and on_thinking:
            on_thinking("Inspecting. ")
        text = "Hello"
        if on_text:
            on_text(text)
        self.release.wait(5)
        if self.ordered:
            tool = '<tool_call>{"name":"read_file","arguments":{"path":"a.py"}}</tool_call>'
            if on_text:
                for character in tool:
                    on_text(character)
            if on_thinking:
                on_thinking("Checking. ")
            text += tool
        if on_text:
            on_text(" world")
        return text + " world", FakeMuseStats(
            thinking="Inspecting. Checking. " if self.ordered else "",
        )


class StreamDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.release = threading.Event()
        self.loaded = []
        self.ordered = False

        def load(config):
            accelerator = GatedAccelerator(self.release, ordered=self.ordered)
            self.loaded.append(accelerator)
            return accelerator

        self.manager = RuntimeManager(loader=load, replicas=2)
        self.server = MachBoostHTTPServer(("127.0.0.1", 0), manager=self.manager)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.manager.close()

    def payload(self, path):
        result = {
            "model": "mlx-community/stream-gate",
            "messages": [{"role": "user", "content": "Say hello"}],
            "stream": True,
            "max_tokens": 32,
            "tools": [{
                "type": "function",
                "function": {"name": "read_file", "parameters": {"type": "object"}},
            }],
        }
        if path == "/v1/messages":
            result["tools"] = [{"name": "read_file", "input_schema": {"type": "object"}}]
        elif path == "/v1/responses":
            result["input"] = "Say hello"
            result["tools"] = [{"type": "function", "name": "read_file", "parameters": {"type": "object"}}]
        return result

    def request(self, path, payload):
        return urlopen(Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        ), timeout=2)

    def read_to_first_text(self, response):
        lines = []
        while True:
            line = response.readline()
            self.assertTrue(line, "stream ended without answer text")
            lines.append(line)
            if b"Hello" in line:
                return b"".join(lines)

    def test_all_chat_protocols_deliver_text_before_generation_finishes_with_tools(self):
        for path in ("/api/chat", "/v1/messages", "/v1/chat/completions", "/v1/responses"):
            with self.subTest(path=path):
                self.release.clear()
                with self.request(path, self.payload(path)) as response:
                    prefix = self.read_to_first_text(response)
                    self.assertFalse(self.release.is_set())
                    self.release.set()
                    body = prefix + response.read()
                if path == "/api/chat":
                    events = [json.loads(line) for line in body.splitlines()]
                    text = "".join(event.get("message", {}).get("content", "") for event in events)
                else:
                    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith(b"data: {")]
                    if path == "/v1/messages":
                        text = "".join(event.get("delta", {}).get("text", "") for event in events)
                    elif path == "/v1/responses":
                        text = "".join(event["delta"] for event in events if event.get("type") == "response.output_text.delta")
                    else:
                        text = "".join(event["choices"][0]["delta"].get("content", "") for event in events)
                self.assertEqual(text, "Hello world")
                self.assertNotIn(b"<tool", body)

    def test_anthropic_blocks_close_before_next_kind_and_tool_is_not_duplicated(self):
        self.ordered = True
        self.release.set()
        with self.request("/v1/messages", self.payload("/v1/messages")) as response:
            body = response.read()
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith(b"data: ")]
        kinds = []
        active = None
        for event in events:
            kind = event["type"]
            if kind == "content_block_start":
                self.assertIsNone(active)
                active = event["index"]
                kinds.append(event["content_block"]["type"])
            elif kind == "content_block_delta":
                self.assertEqual(event["index"], active)
            elif kind == "content_block_stop":
                self.assertEqual(event["index"], active)
                active = None
        self.assertIsNone(active)
        self.assertEqual(kinds, ["thinking", "text", "tool_use", "thinking", "text"])
        self.assertEqual(events[-1]["type"], "message_stop")

    def test_concurrent_native_and_anthropic_streams_do_not_buffer_each_other(self):
        with self.request("/api/chat", self.payload("/api/chat")) as native:
            self.read_to_first_text(native)
            with self.request("/v1/messages", self.payload("/v1/messages")) as claude:
                self.read_to_first_text(claude)
                self.assertEqual(self.manager.ps()[0]["scheduler"]["active_requests"], 2)
                self.release.set()
                native.read()
                claude.read()
        self.assertEqual(len(self.loaded), 2)
        # Follow-ups use the same resident resources, not another weight load.
        with self.request("/api/chat", self.payload("/api/chat")) as response:
            response.read()
        self.assertEqual(len(self.loaded), 2)

    def test_cancel_tool_enabled_stream_releases_request_without_late_answer(self):
        payload = {**self.payload("/v1/messages"), "request_id": "cancel-stream-test"}
        with self.request("/v1/messages", payload) as response:
            self.read_to_first_text(response)
            with self.request("/api/cancel", {"request_id": "cancel-stream-test"}) as cancelled:
                self.assertTrue(json.load(cancelled)["cancelled"])
            self.release.set()
            body = response.read()
        self.assertIn(b"request cancelled", body)
        self.assertNotIn(b"world", body)
        self.assertEqual(self.manager.ps()[0]["scheduler"]["active_requests"], 0)
