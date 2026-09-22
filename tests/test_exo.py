import io
import json
import threading
import unittest
from argparse import Namespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from examples.python.benchmark_exo_cluster import run_benchmark

from machboost.exo import (
    ExoClusterClient,
    ExoClusterError,
    _sse_events,
    parse_cluster_state,
    require_placement,
    require_rdma_placement,
)


MODEL = "mlx-community/Llama-3.2-1B-Instruct-4bit"


def cluster_state(*, nodes=("studio", "laptop"), sharding="TensorShardMetadata"):
    shards = {
        f"runner-{index}": {sharding: {"deviceRank": index}}
        for index, _ in enumerate(nodes)
    }
    return {
        "topology": {
            "nodes": list(nodes),
            "connections": {
                "studio": {"laptop": [{"RDMAConnection": {"bandwidth": 1}}]}
            },
        },
        "instances": {
            "instance-1": {
                "MlxJacclInstance": {
                    "instanceId": "instance-1",
                    "shardAssignments": {
                        "modelId": MODEL,
                        "nodeToRunner": {
                            node: f"runner-{index}" for index, node in enumerate(nodes)
                        },
                        "runnerToShard": shards,
                    },
                }
            }
        },
    }


class ExoStateTests(unittest.TestCase):
    def test_tagged_cluster_state_identifies_rdma_and_tensor_placement(self):
        snapshot = parse_cluster_state(cluster_state())

        self.assertEqual(snapshot["nodes"], ["laptop", "studio"])
        self.assertEqual(snapshot["rdma_links"], [["laptop", "studio"]])
        placement = require_placement(snapshot, MODEL)
        self.assertEqual(placement["sharding"], "tensor")
        self.assertEqual(placement["transport"], "jaccl")
        self.assertEqual(len(placement["nodes"]), 2)

    def test_single_node_and_pipeline_are_not_mislabeled_as_fast_cluster(self):
        single = parse_cluster_state(cluster_state(nodes=("studio",)))
        pipeline = parse_cluster_state(cluster_state(sharding="PipelineShardMetadata"))

        with self.assertRaisesRegex(ExoClusterError, "2\\+ EXO nodes"):
            require_placement(single, MODEL)
        with self.assertRaisesRegex(ExoClusterError, "tensor instance"):
            require_placement(pipeline, MODEL)

    def test_unknown_instance_shape_fails_closed(self):
        state = cluster_state()
        state["instances"]["instance-1"] = {"FutureInstance": {}}
        with self.assertRaisesRegex(ExoClusterError, "unrecognized instance type"):
            parse_cluster_state(state)

    def test_multiple_same_model_placements_are_not_assumed_to_route_to_cluster(self):
        state = cluster_state()
        state["instances"]["single"] = {
            "MlxRingInstance": {
                "instanceId": "single",
                "shardAssignments": {
                    "modelId": MODEL,
                    "nodeToRunner": {"studio": "runner-0"},
                    "runnerToShard": {"runner-0": {"TensorShardMetadata": {}}},
                },
            }
        }
        with self.assertRaisesRegex(ExoClusterError, "scheduler may choose"):
            require_placement(parse_cluster_state(state), MODEL)

    def test_rdma_must_connect_the_placement_not_unrelated_nodes(self):
        snapshot = parse_cluster_state(cluster_state())
        placement = require_placement(snapshot, MODEL)
        require_rdma_placement(snapshot, placement)
        snapshot["rdma_links"] = [["studio", "third-machine"]]
        with self.assertRaisesRegex(ExoClusterError, "not connected by RDMA"):
            require_rdma_placement(snapshot, placement)
        placement["transport"] = "ring"
        with self.assertRaisesRegex(ExoClusterError, "does not use JACCL"):
            require_rdma_placement(snapshot, placement)

    def test_sse_parser_handles_multiline_data_and_comments(self):
        data = io.BytesIO(b': prefill_progress {"done":1}\n\ndata: {"one":\n' b'data: 2}\n\ndata: [DONE]\n\n')
        self.assertEqual(
            list(_sse_events(iter(data))),
            [("comment", 'prefill_progress {"done":1}'), ("data", '{"one":\n2}'), ("data", "[DONE]")],
        )


class FakeExoHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/state":
            body = json.dumps(self.server.state).encode()
        elif self.path == "/api/ps":
            body = json.dumps({"models": [{"model": MODEL}]}).encode()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        self.server.last_request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.last_authorization = self.headers.get("Authorization")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if self.server.fail_stream:
            self.wfile.write(b'data: {"error":{"message":"inference failed"}}\n\n')
            return
        if self.server.empty_stream:
            self.wfile.write(b'data: [DONE]\n\n')
            return
        for event in (
            ': prefill_progress {"step":1}\n\n',
            'data: {"choices":[{"delta":{"reasoning_content":"checking"}}]}\n\n',
            'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
            'data: {"choices":[{"delta":{"content":" world"},"finish_reason":"stop"}],"usage":{"prompt_tokens":11,"completion_tokens":4}}\n\n',
            ': generation_stats {"generation_tps":80.0,"prefix_cache_hit":"partial"}\n\n',
            'data: [DONE]\n\n',
        ):
            if self.server.truncate and event == 'data: [DONE]\n\n':
                return
            self.wfile.write(event.encode())
            self.wfile.flush()


class ExoClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeExoHandler)
        cls.server.state = cluster_state()
        cls.server.fail_stream = False
        cls.server.truncate = False
        cls.server.empty_stream = False
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.server.fail_stream = False
        self.server.truncate = False
        self.server.empty_stream = False
        self.server.state = cluster_state()
        self.client = ExoClusterClient(
            f"http://127.0.0.1:{self.server.server_port}", api_key="private-test-key"
        )

    def test_real_http_contract_probe_and_stream_measurement(self):
        snapshot = self.client.snapshot()
        self.assertEqual(require_placement(snapshot, MODEL)["id"], "instance-1")

        measured = self.client.measure_chat(
            MODEL, [{"role": "user", "content": "Say hello"}], max_tokens=16
        )

        self.assertEqual(measured.content, "Hello world")
        self.assertEqual(measured.reasoning, "checking")
        self.assertEqual(measured.prompt_tokens, 11)
        self.assertEqual(measured.completion_tokens, 4)
        self.assertEqual(measured.generation_tokens_per_second, 80.0)
        self.assertEqual(measured.prefix_cache_hit, "partial")
        self.assertEqual(measured.finish_reason, "stop")
        self.assertLessEqual(measured.first_output_seconds, measured.first_answer_seconds)
        self.assertLessEqual(measured.first_answer_seconds, measured.total_seconds)
        self.assertEqual(self.server.last_authorization, "Bearer private-test-key")
        self.assertEqual(self.server.last_request["model"], MODEL)
        self.assertTrue(self.server.last_request["stream"])

    def test_upstream_error_and_truncated_stream_are_not_successes(self):
        self.server.fail_stream = True
        with self.assertRaisesRegex(ExoClusterError, "inference failed"):
            self.client.measure_chat(MODEL, [{"role": "user", "content": "hi"}])

        self.server.fail_stream = False
        self.server.truncate = True
        with self.assertRaisesRegex(ExoClusterError, "before \\[DONE\\]"):
            self.client.measure_chat(MODEL, [{"role": "user", "content": "hi"}])

        self.server.truncate = False
        self.server.empty_stream = True
        with self.assertRaisesRegex(ExoClusterError, "without model output"):
            self.client.measure_chat(MODEL, [{"role": "user", "content": "hi"}])

    def test_invalid_endpoint_is_rejected(self):
        for endpoint in ("localhost:52415", "ftp://localhost:52415", "http://user:pass@localhost:52415"):
            with self.assertRaises(ValueError):
                ExoClusterClient(endpoint)

    def test_paired_benchmark_alternates_order_and_omits_prompt_and_outputs(self):
        endpoint = f"http://127.0.0.1:{self.server.server_port}"
        args = Namespace(
            exo_url=endpoint,
            machboost_url=endpoint,
            model=MODEL,
            prompt="private fixture phrase",
            runs=2,
            warmups=0,
            max_tokens=16,
            timeout=120,
            temperature=0,
            seed=0,
            min_nodes=2,
            sharding="tensor",
            require_rdma=True,
            fresh=True,
        )
        with patch.dict("os.environ", {"MACHBOOST_API_TOKEN": "fixture-key"}):
            report = run_benchmark(args)

        self.assertEqual(report["schema"], "machboost.exo_compare.v1")
        self.assertEqual([pair["first"] for pair in report["pairs"]], ["exo", "machboost"])
        self.assertEqual(report["summary"]["answer_equal_pairs"], 2)
        self.assertNotIn("private fixture phrase", json.dumps(report))
        self.assertNotIn("Hello world", json.dumps(report))
        self.assertIn("answer_sha256", report["pairs"][0]["exo"])

    def test_benchmark_requires_active_multi_node_placement(self):
        self.server.state = cluster_state(nodes=("studio",))
        snapshot = self.client.snapshot()
        with self.assertRaisesRegex(ExoClusterError, "2\\+ EXO nodes"):
            require_placement(snapshot, MODEL)


if __name__ == "__main__":
    unittest.main()
