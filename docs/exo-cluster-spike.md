# EXO cluster validation spike

Status: experimental branch only. This does not change MachBoost's default inference engine, app, or server. No EXO package is installed by MachBoost.

## Why this boundary

[EXO](https://github.com/exo-explore/exo) is a separate inference service, not an MLX speed-up function that can be dropped into a process. Its cluster uses model placement, runner processes, network discovery, and its own API. This spike measures an externally managed EXO placement through HTTP before considering an in-app integration. Upstream protocol assumptions were checked against EXO commit `21a54c5ea0230a3bec1e1a786d200126c7e34ec6` and may need revision when EXO changes.

The optional client in `machboost/exo.py` reads `GET /state` and streams `POST /v1/chat/completions`. The benchmark requires one placement of the requested model spanning at least two EXO nodes. By default it requires tensor sharding. It can additionally require a JACCL placement with RDMA connectivity between all its nodes. These checks prevent an obvious single-device result from being reported as a distributed result; they do not prove the hardware is using Thunderbolt or that the placement is faster.

EXO schedules among available instances of a model. The benchmark rejects multiple instances of the same model because `/v1/chat/completions` does not pin the request to a specific instance. An EXO upgrade that changes this behavior requires revalidation.

## Run on two Macs

1. Install and start EXO separately on both Macs, using EXO's current setup instructions. Confirm the model has a single, multi-node tensor placement in EXO. Use a model that both EXO and MachBoost can run with the **same repository ID and weight format**. Check EXO's `GET /state` before benchmarking.
2. If a Thunderbolt bridge is present, verify it in macOS and EXO. The `--require-rdma` check uses EXO's reported RDMA connections; it is not a link-level throughput test.
3. On the single-Mac baseline, start MachBoost and warm that exact model. Wait until `machboost ps` shows it resident and no longer warming. Run the two endpoints on separate ports.
4. From this repository root, using a Python environment with MachBoost's normal dependencies, run:

   ```sh
   python3 -m examples.python.benchmark_exo_cluster \
     --exo-url http://127.0.0.1:52415 \
     --machboost-url http://127.0.0.1:11435 \
     --model REPLACE_WITH_EXACT_REPOSITORY_ID \
     --warmups 2 --runs 10 --max-tokens 256 \
     --require-rdma --output /tmp/exo-compare.json
   ```

   Set `EXO_API_TOKEN` and `MACHBOOST_API_TOKEN` in the environment if the respective servers require them. Do not place tokens in command arguments, result files, or shared logs. Local MachBoost app credentials can also be read from its existing local credential store.

Each pair uses the same prompt on both engines, alternates engine order, and adds a fresh leading request ID by default. Use `--reuse-prompt` only to investigate repeated-prefix behavior. The JSON report stores prompt/output hashes and lengths, not raw text; it does include EXO node identifiers, so review it before sharing. The script checks that EXO's selected placement remains unchanged through the run. Inspect whether the model, prompt template, quantization, context settings, and generated token counts are actually comparable before treating any ratio as a speedup. Output equality is reported; it is not guaranteed by a shared seed or model name. First output and first visible answer are separate metrics, since reasoning can precede an answer.

Run a second experiment with a longer context and a third with several simultaneous clients. The current script measures **serial single-request pairs only**. It does not measure team throughput, queueing, cross-host failover, or multi-model routing.

## Acceptance before integration

- Record EXO version, MachBoost version, macOS and chip, node count, topology/RDMA state, exact model artifact and quantization, and endpoint placement.
- Repeat cold and resident tests, with 10 or more paired runs in each condition. Report medians and spread for first output, first answer, total wall time, generated tokens, and output equality. Count errors and timeouts rather than dropping failed runs.
- Compare against the same single-Mac model in MachBoost and a second native baseline if available. A distributed win on one workload is not a universal decoding-speed win.
- Verify cancellation, model unload, auth boundaries, and recovery when a node disappears before adding EXO to the app or CLI as a user-facing backend.

The protocol tests use a local fake HTTP server. They establish parsing and failure behavior, not EXO performance. A real two-node result is still required.
