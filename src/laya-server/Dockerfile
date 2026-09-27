# syntax=docker/dockerfile:1

# The Laya inference service (ADR-0002, ARCHITECTURE §5): a multi-stage build
# whose builder exports the pinned `multilingual/` checkpoint to INT8 ONNX and
# whose runtime ships only the ONNX model, the config and the tokenizer.

ARG PYTHON_VERSION=3.12

##############################################################################
# Builder: torch CPU + laya[export] + the pinned checkpoint -> INT8 ONNX
##############################################################################
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

# The pinned revision of `convaiinnovations/laya` the weights are downloaded at.
# Resolved from the Hugging Face API; ARCHITECTURE §5 quotes its `55cf4c4e…` prefix.
ARG LAYA_REVISION=55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851
# The revision of the Laya repo (github.com/NandhaKishorM/laya) that ships
# scripts/export_onnx.py. The script is not included in the PyPI sdist, so the
# builder fetches exactly this checkout.
ARG LAYA_EXPORT_REVISION=9d955671415fc19f069b9cc998928075c1f255ec
ARG LAYA_PACKAGE_VERSION=0.3.21

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# CPU torch first: without it `pip install laya[...]` resolves the default
# PyPI torch, which pulls in the CUDA stacks (several GB, none of it usable
# here). Pre-installing the CPU wheel satisfies laya's `torch>=2.0.0` and pip
# keeps it.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

RUN pip install "laya[serve,onnx]==${LAYA_PACKAGE_VERSION}"

# The export script from the pinned Laya repo checkout (the revision is part
# of the URL, so the content cannot drift).
ADD "https://raw.githubusercontent.com/NandhaKishorM/laya/${LAYA_EXPORT_REVISION}/scripts/export_onnx.py" /laya-repo/scripts/export_onnx.py

# Download only the multilingual checkpoint files at the pinned revision:
# config, weights, tokenizer. Nothing else from the bundle is fetched.
RUN LAYA_REVISION="${LAYA_REVISION}" python - <<'EOF'
import os

from huggingface_hub import snapshot_download

snapshot_download(
    "convaiinnovations/laya",
    revision=os.environ["LAYA_REVISION"],
    local_dir="/ckpt",
    allow_patterns=[
        "multilingual/rl_agent_config.json",
        "multilingual/model.safetensors",
        "multilingual/tokenizer/*",
    ],
)
print("downloaded multilingual checkpoint at", os.environ["LAYA_REVISION"])
EOF

# Export fp32 ONNX in its own torch process, then run the Laya repo's INT8
# quantizer in a fresh, torch-free process: export and quantize in one
# process peaked past the memory a Docker Desktop VM grants (4.8 GB here) and
# was OOM-killed; apart, each step fits.
RUN python /laya-repo/scripts/export_onnx.py \
    --model /ckpt/multilingual \
    --output /export/laya.onnx

# The Laya script's quantize_model loads the fp32 graph and hands ORT's
# quantize_dynamic a ModelProto, which makes ORT deepcopy the whole ~650 MB
# graph; in a 4.8 GB Docker Desktop VM that step was OOM-killed. These two
# RUNs make the identical onnxruntime calls with the script's identical
# arguments (del value_info, MatMul-only, QInt8, per-channel), minus the
# in-memory copy: quantize_dynamic gets a path, so ORT streams shape
# inference through infer_shapes_path instead of copying the graph.
RUN python -c "import onnx; m = onnx.load('/export/laya.onnx'); del m.graph.value_info[:]; onnx.save(m, '/export/laya.novi.onnx'); del m" \
    && rm -f /export/laya.onnx

RUN python -c "from onnxruntime.quantization import QuantType, quantize_dynamic; quantize_dynamic('/export/laya.novi.onnx', '/export/laya.int8.onnx', op_types_to_quantize=['MatMul'], weight_type=QuantType.QInt8, per_channel=True)" \
    && rm -f /export/laya.novi.onnx

# Keep only the runtime artefacts: the INT8 ONNX graph, the config and the
# tokenizer. The intermediate fp32 files, the fp32 safetensors and the rest
# of the HF snapshot stay in this stage.
RUN mkdir -p /out/multilingual/tokenizer \
    && cp /export/laya.int8.onnx /out/multilingual/ \
    && cp /ckpt/multilingual/rl_agent_config.json /out/multilingual/ \
    && cp /ckpt/multilingual/tokenizer/tokenizer.json /out/multilingual/tokenizer/ \
    && cp /ckpt/multilingual/tokenizer/tokenizer_config.json /out/multilingual/tokenizer/

##############################################################################
# Runtime: CPU torch + laya[serve,onnx] + the exported artefacts + app.py
##############################################################################
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

# torch is needed at run time: laya.common (imported by laya.onnx_agent at
# module level) and laya.agent (imported by ONNXAgent to reuse its question
# validation) both `import torch` unconditionally. Verified: an environment
# with laya[serve,onnx] but no torch fails at import with
# `ModuleNotFoundError: No module named 'torch'`. See ADR-0002.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

RUN pip install "laya[serve,onnx]==0.3.21"

RUN useradd --system --uid 10001 --home-dir /nonexistent laya

COPY --from=builder --chown=laya:laya /out /models/laya

WORKDIR /app
COPY --chown=laya:laya app.py .

# The service is hermetic: every artefact it serves is baked into the image,
# so Hugging Face access is switched off entirely. This is also the backstop
# that guarantees a request for an unshipped checkpoint can never start an
# English download (app.py already refuses those on disk).
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    LAYA_MODELS_DIR=/models/laya \
    LAYA_PORT=8000 \
    PIP_NO_CACHE_DIR=1

USER laya
EXPOSE 8000

CMD ["python", "app.py"]
