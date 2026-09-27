# Roadmap

Deliberately left out of v1. The order is not a priority ranking.

## Classification quality

- **Eval set and scoring script** (`eval/`): 300–500 labelled RU/EN messages covering spam, ads, insults, clean messages and edge cases, such as swearing that isn't an insult or price talk that isn't advertising. It would report precision/recall per Category, calibration (ECE), and CPU latency. It would also serve as a regression test whenever the backend, checkpoint or question wording changes.
- **Calibrated Sensitivity thresholds** from the eval set, to replace the starting values.
- **ONNX INT8 vs torch fp32 accuracy comparison.** v1 uses INT8 without checking it.
- **Labelled sample collection.** An Operator flag (`RETAIN_LABELED_SAMPLES`) would keep message texts that carry an Admin decision past the retention window, with JSONL export for fine-tuning. v1 collects nothing.
- **Fine-tuning Laya** on the collected samples.

## Features

- Custom Categories. The schema is ready.
- Sensitivity set separately per Category. The schema is ready.
- Checking attachment content (images, voice) when there is no caption.
- A manual per-chat allowlist of Members.
- A separate Notice Template for each Category.
- Warning the Admin when an external Classifier Backend is switched on, since chat messages will be sent to a third party, and logging who switched it on and when.
- Appeals for Members restricted during a violation burst whose Chat Notice was dropped.

## Classifier Backends

- Jev via Cloudflare Workers AI. It differs from TypeSafe and OpenRouter:
  - the path is `/accounts/{id}/ai/run`
  - an account id is required
  - the request body is wrapped in `input`
  - the model id is `typesafe/jev`
  - REST responses are probably wrapped in `result`

  It needs its own adapter.

## Delivery

- Full CI/CD: build and publish the `bot` and `laya-server` Docker images. v1 CI runs only `ruff` and `pytest`, because building the Laya image is too heavy for every PR.
- A Laya smoke test in CI: start the container and send one real request.

## Scaling

- A public multi-tenant Instance on the same codebase (ADR-0001). It would need per-chat Jev API keys: entered through the Menu, stored encrypted, and masked in the UI.
- Horizontal scaling of the classifier service.
