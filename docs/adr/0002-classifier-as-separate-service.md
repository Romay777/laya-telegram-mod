# One System One client, with Laya as a separate inference service

The bot checks messages through a Classifier Backend interface that takes text plus signals and returns a Verdict. Laya's `laya-serve` implements the same HTTP protocol as TypeSafe Jev: `POST /v1/systemone` with `state` and `questions`, returning `answers` with per-label `probabilities`, and Bearer auth. Because of that, v1 has a single System One client in the bot. Each backend is just a connection: a base URL, an optional key, and a pinned model.

- **Laya** is reached at `http://laya:8000/v1`, always with `model: "multilingual"`.
- **Jev** is reached at `JEV_BASE_URL` with `JEV_API_KEY` and `JEV_MODEL`. The same client works against TypeSafe and OpenRouter.

Categories are described once, as a versioned question spec in the bot's code. The client handles transport and normalises answers into Verdicts.

Laya runs in its own container (`src/laya-server/`). It is a thin wrapper around `laya-serve` that loads the `multilingual/` checkpoint as INT8 ONNX. The weights are downloaded at a pinned HF revision and exported to ONNX at image build time, so they are baked into the image. The container has no published port and is reachable only on the internal Docker network.

**Torch stays in the runtime image (verified).** The ONNX runtime path needs torch installed: `laya.onnx_agent` imports `laya.common` at module level, and `laya/common.py` does `import torch` (line 12) outside any lazy hook; `ONNXAgent` also imports `laya.agent` (torch-backed) for tokenizer fixing and question validation, and `laya`'s own package metadata requires `torch>=2.0.0` unconditionally. Verified empirically against laya 0.3.21 in an environment that had `laya`, `onnxruntime` and `transformers` but no torch: `import laya.onnx_agent` fails with `ModuleNotFoundError: No module named 'torch'` (chain `laya/onnx_agent.py:15 → laya/common.py:12`). The runtime stage therefore installs the CPU-only torch wheel (`pip install torch --index-url https://download.pytorch.org/whl/cpu`, done before `laya[serve,onnx]` so pip keeps it), and dropping torch entirely is not possible without forking the package.

Laya gets its own process because the model is heavy: about 1–2 GB of RAM and ~200 ms of CPU time per question. Inside aiogram it would block the event loop.

## Considered Options

- **Model inside the bot process** (via an executor). Rejected: the bot and model would share RAM and the GIL, a bot restart would reload the model, and they could not be scaled separately.
- **Stock `laya-serve` with torch fp32.** Rejected: slower and heavier. It cannot serve ONNX, which is why we have the thin wrapper.
- **Downloading weights into a volume on first start.** Rejected: the ONNX export needs torch and the export script from the Laya repo, so it belongs at build time. As a bonus, first start does not depend on Hugging Face.
- **A check queue in Postgres.** Rejected: a stale check is useless. The bot calls the backend directly with a timeout and a concurrency limit, and skips the check when the backend is saturated.

## Consequences

- When overloaded, the bot skips checks rather than queueing them. Deleting a message minutes after it was sent is worse than missing one spam message.
- If Jev fails, the bot falls back to Laya when it is deployed and otherwise skips the check.
- The Laya container is a Compose profile that is enabled by default. An Instance can run with Jev only.
- The Laya image is large. Measured at build time (arm64, torch 2.14 CPU, laya 0.3.21): 3.78 GB, of which the CPU torch wheel is the bulk and the quantized checkpoint 916 MB (the INT8 graph quantizes the 98 MatMul weights; the 256k-vocabulary embeddings and every non-MatMul op stay fp32, so "INT8" does not mean a 4x smaller model).
- Versions are pinned because Sensitivity thresholds are tuned per backend version:
  - Laya is pinned by package version (`laya==0.3.21`, the first release that supports revision pinning) and by weight revision.
  - Jev is pinned to a model version, not `jev-latest`.
- A Cloudflare Workers AI connection does not fit this client, because it has a different path and wraps requests and responses. It is on the ROADMAP.
