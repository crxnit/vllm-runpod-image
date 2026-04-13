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

# Ensure CUDA and NVIDIA driver libraries are on the dynamic-linker search path.
# /usr/local/nvidia/lib64 contains libnvidia-ml.so (NVML) which is bind-mounted
# from the host by RunPod after container start.  It must be on LD_LIBRARY_PATH
# so vLLM's platform detection can find it when VllmConfig() is instantiated
# during argparse setup (before --device cuda is parsed from the command line).
export LD_LIBRARY_PATH="/usr/local/nvidia/lib64:/usr/local/cuda/compat:/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# Tell vLLM to target CUDA without relying on NVML auto-detection.
# vLLM's argument-parser setup instantiates VllmConfig() to compute default
# values; that triggers device inference, which calls into NVML.  If the
# NVIDIA driver bind-mount hasn't completed yet (common on RunPod start),
# NVML reports "Shared Library Not Found" and vLLM raises:
#   RuntimeError: Failed to infer device type
# Setting this env var bypasses the inference and hard-codes the target device.
export VLLM_TARGET_DEVICE="${VLLM_TARGET_DEVICE:-cuda}"

# Wait for the NVIDIA driver bind-mount to complete before invoking vllm.
# RunPod mounts /usr/local/nvidia (containing libnvidia-ml.so) from the host
# after container start.  vLLM's argparse setup calls VllmConfig() as a default
# factory during __init__, which immediately checks NVML.  If the mount isn't
# done yet, NVML reports "Shared Library Not Found" and the CLI fails before
# --device cuda is even parsed.  Polling nvidia-smi ensures the driver is
# accessible before we attempt to start the server.
echo "[start.sh] Waiting for NVIDIA driver to become available..."
for i in $(seq 1 15); do
    if nvidia-smi --query-gpu=name --format=csv,noheader &>/dev/null; then
        GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
        echo "[start.sh] GPU available: ${GPU_NAME}"
        break
    fi
    if [[ $i -eq 15 ]]; then
        echo "[start.sh] ERROR: GPU not available after 30 seconds. Aborting."
        exit 1
    fi
    echo "[start.sh] Attempt ${i}/15: NVIDIA driver not yet available, retrying in 2s..."
    sleep 2
done

# Re-run ldconfig after the NVIDIA driver bind mount is confirmed available so
# that libnvidia-ml.so and libcudart.so are registered for the dynamic linker.
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
