#!/bin/bash
set -euo pipefail

# =============================================================================
# vLLM server startup script — RunPod-ready
#
# Set these as Environment Variables in your RunPod pod/template:
#
#   MODEL_NAME       HuggingFace model ID to serve
#                    default: Qwen/Qwen2.5-7B-Instruct
#
#   HF_TOKEN         HuggingFace token for gated/private models (optional)
#
#   MAX_MODEL_LEN    Max context length in tokens
#                    default: 4096
#
#   GPU_MEMORY_UTIL  Fraction of GPU VRAM to allocate to the model (0.0–1.0)
#                    default: 0.90
#
#   TENSOR_PARALLEL  Number of GPUs to shard across (tensor parallelism).
#                    Defaults to RUNPOD_GPU_COUNT when running on RunPod,
#                    otherwise 1.
#
#   DTYPE            Model weight dtype: auto | float16 | bfloat16
#                    default: auto
#
#   ENABLE_LORA      Set to "true" to enable LoRA adapter support
#                    default: false
#
#   LORA_PATH        HuggingFace repo ID or local path of a LoRA adapter.
#                    Only used when ENABLE_LORA=true.
#
#   API_KEY          Bearer token clients must send as Authorization header.
#                    Matches the --api-key flag in classify_core.py.
#                    Leave unset to allow unauthenticated access.
#
#   HOST             Bind address (default: 0.0.0.0)
#   PORT             Port to listen on (default: 8000)
#
#   SKIP_PIP_INSTALL Set to "true" to skip the runtime pip install step.
#                    Default: false — packages are installed at startup so that
#                    vllm/vllm-openai:latest can be used directly on RunPod
#                    without building or pushing a custom Docker image.
#                    Set to "true" when using an image built via build.sh
#                    (packages are already baked in).
# =============================================================================

SKIP_PIP_INSTALL="${SKIP_PIP_INSTALL:-false}"
MODEL_NAME="${MODEL_NAME:-Qwen/Qwen2.5-7B-Instruct}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-4096}"
GPU_MEMORY_UTIL="${GPU_MEMORY_UTIL:-0.90}"
DTYPE="${DTYPE:-auto}"
ENABLE_LORA="${ENABLE_LORA:-false}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

# Auto-detect GPU count from RunPod's injected env var; fall back to 1.
TENSOR_PARALLEL="${TENSOR_PARALLEL:-${RUNPOD_GPU_COUNT:-1}}"

# ---------------------------------------------------------------------------
# Runtime dependency installation
# Installs packages required by the accounting-classification scripts so that
# the public vllm/vllm-openai:latest image can be used on RunPod without
# building or pushing a custom Docker image.
# Skip by setting SKIP_PIP_INSTALL=true (e.g. when using an image from build.sh).
# ---------------------------------------------------------------------------
if [[ "${SKIP_PIP_INSTALL}" != "true" ]]; then
    echo "Installing Python dependencies..."
    pip install --quiet --no-cache-dir \
        requests \
        pandas \
        transformers \
        peft \
        trl \
        datasets \
        accelerate \
        bitsandbytes
    echo "Dependencies installed."
fi

echo "============================================================"
echo " vLLM server starting"
echo " Model          : ${MODEL_NAME}"
echo " Max seq len    : ${MAX_MODEL_LEN}"
echo " GPU mem util   : ${GPU_MEMORY_UTIL}"
echo " Tensor parallel: ${TENSOR_PARALLEL}"
echo " LoRA enabled   : ${ENABLE_LORA}"
echo " Listening on   : ${HOST}:${PORT}"
echo "============================================================"

# HuggingFace token for gated/private models
if [[ -n "${HF_TOKEN:-}" ]]; then
    export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"
    echo " HF_TOKEN set — authenticated HuggingFace downloads enabled"
fi

ARGS=(
    --model              "${MODEL_NAME}"
)

# Optional bearer-token auth (passed as --api-key to classify scripts via --api-key flag)
if [[ -n "${API_KEY:-}" ]]; then
    ARGS+=(--api-key "${API_KEY}")
    echo " API_KEY set — endpoint requires Authorization: Bearer <key>"
fi

# LoRA adapter support
if [[ "${ENABLE_LORA}" == "true" ]]; then
    ARGS+=(--enable-lora)
    if [[ -n "${LORA_PATH:-}" ]]; then
        ARGS+=(--lora-modules "adapter=${LORA_PATH}")
        echo " LoRA adapter : ${LORA_PATH}"
    fi
fi

echo "============================================================"

vllm serve "${MODEL_ID}" —enable-lora —lora-modules accounting-classifier=lora_output/adapter

Python3 classify.py —backend vllm —model accounting-classifier —input transactions.csv &
