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
#   ENABLE_LORA      Set to "true" to enable LoRA adapter support
#                    default: false
#
#   LORA_PATH        HuggingFace repo ID or local path of a LoRA adapter.
#                    Only used when ENABLE_LORA=true.
#
#   HOST             Bind address (default: 0.0.0.0)
#   PORT             Port to listen on (default: 8000)
#
# =============================================================================

# Ensure the CUDA compat stub (libcuda.so.1) and CUDA libs are on the
# dynamic-linker search path.  The Dockerfile already calls ldconfig, but
# exporting LD_LIBRARY_PATH here guards against environments where the
# container is run without the custom image (e.g. bare vllm/vllm-openai).
export LD_LIBRARY_PATH="/usr/local/cuda/compat:/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# RunPod bind-mounts /usr/local/cuda from the host at container start, after
# the Dockerfile's ldconfig ran.  Re-run it now so libcudart.so is registered
# and bitsandbytes/PyTorch can find the CUDA runtime via find_library('cudart').
ldconfig 2>/dev/null || true

MODEL_NAME="${MODEL_NAME:-Qwen/Qwen2.5-7B-Instruct}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-4096}"
GPU_MEMORY_UTIL="${GPU_MEMORY_UTIL:-0.90}"
DTYPE="${DTYPE:-auto}"
ENABLE_LORA="${ENABLE_LORA:-false}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

# Auto-detect GPU count from RunPod's injected env var; fall back to 1.
TENSOR_PARALLEL="${TENSOR_PARALLEL:-${RUNPOD_GPU_COUNT:-1}}"

# Enable verbose vLLM logging (can be overridden at runtime by setting VLLM_LOGGING_LEVEL).
export VLLM_LOGGING_LEVEL="${VLLM_LOGGING_LEVEL:-DEBUG}"

# ---------------------------------------------------------------------------
# Runtime dependency installation
# Installs packages required by the accounting-classification scripts so that
# the public vllm/vllm-openai:latest image can be used on RunPod without
# building or pushing a custom Docker image.
# ---------------------------------------------------------------------------

# HuggingFace token for gated/private models
if [[ -n "${HF_TOKEN:-}" ]]; then
    export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"
    echo " HF_TOKEN set — authenticated HuggingFace downloads enabled"
fi

ARGS=(
    --model            "${MODEL_NAME}"
    --device           cuda
    --dtype            "${DTYPE}"
    --max-model-len    "${MAX_MODEL_LEN}"
    --gpu-memory-utilization "${GPU_MEMORY_UTIL}"
    --tensor-parallel-size   "${TENSOR_PARALLEL}"
    --host             "${HOST}"
    --port             "${PORT}"
)

# LoRA adapter support
if [[ "${ENABLE_LORA}" == "true" ]]; then
    ARGS+=(--enable-lora)
    if [[ -n "${LORA_PATH:-}" ]]; then
        ARGS+=(--lora-modules "adapter=${LORA_PATH}")
    fi
fi

exec vllm serve "${ARGS[@]}"
