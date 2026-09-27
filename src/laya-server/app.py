"""The Laya inference service: one ONNX checkpoint behind laya-serve's System One protocol.

The container image ships exactly one runtime artefact set: the `multilingual/`
checkpoint of `convaiinnovations/laya`, exported to INT8 ONNX at build time
(Dockerfile). `build_router` wraps it in an `ONNXAgent`, attaches it to a
`laya.Router` as `multilingual`, and `laya.serve.create_app` serves it on the
Jev-compatible `/v1/systemone` protocol on port 8000. Requests reach it only on
the internal Docker network; the bot always pins `model: "multilingual"`.
"""

import os
from collections.abc import Callable
from typing import Any

from laya.router import Router
from laya.serve import create_app

DEFAULT_MODELS_DIR = "/models/laya"
MULTILINGUAL_SUBFOLDER = "multilingual"
ONNX_FILENAME = "laya.int8.onnx"

# The image ships no English or typed-decisions checkpoint. Their Router entries
# point at nonexistent absolute paths, so `Router.load` refuses them on disk
# (`Agent` raises FileNotFoundError for missing local paths) before any Hugging
# Face download could start. The runtime image also sets HF_HUB_OFFLINE=1, so
# even a misconfiguration cannot fetch the English checkpoint at run time.
_DISABLED_CHECKPOINT_DIR = "/models/disabled"
_DISABLED_CHECKPOINTS = ("english", "typed-decisions")


def build_router(
    models_dir: str | None = None,
    agent_factory: Callable[[], Any] | None = None,
) -> Router:
    """Build the single-checkpoint Router the server serves.

    `models_dir` is the directory holding the runtime artefacts:
    `<models_dir>/multilingual/{laya.int8.onnx, rl_agent_config.json, tokenizer/}`.
    `agent_factory` exists so tests can attach a scripted agent instead of the
    real `ONNXAgent`.
    """
    models_dir = models_dir or os.environ.get("LAYA_MODELS_DIR", DEFAULT_MODELS_DIR)
    if agent_factory is None:
        onnx_path = os.path.join(models_dir, MULTILINGUAL_SUBFOLDER, ONNX_FILENAME)

        def agent_factory() -> Any:
            from laya.onnx_agent import ONNXAgent

            return ONNXAgent(
                model_id_or_path=models_dir,
                onnx_path=onnx_path,
                subfolder=MULTILINGUAL_SUBFOLDER,
            )

    agent = agent_factory()
    router = Router(
        models={
            name: f"{_DISABLED_CHECKPOINT_DIR}/{name}" for name in _DISABLED_CHECKPOINTS
        },
        default=MULTILINGUAL_SUBFOLDER,
        max_loaded=1,
    )
    router.attach(MULTILINGUAL_SUBFOLDER, agent)
    return router


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_app(router=build_router()),
        host=os.environ.get("LAYA_HOST", "0.0.0.0"),
        port=int(os.environ.get("LAYA_PORT", "8000")),
        log_level=os.environ.get("LAYA_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
