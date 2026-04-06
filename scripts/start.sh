#!/bin/bash
set -e

MAX_LEN="${MAX_MODEL_LEN:-16384}"
GPU_MEM="${GPU_MEMORY_UTILIZATION:-0.95}"
MAX_SEQS="${MAX_NUM_SEQS:-64}"

# Validate env vars are numeric to prevent injection
if ! [[ "$MAX_LEN" =~ ^[0-9]+$ ]]; then
    echo "ERROR: MAX_MODEL_LEN must be a positive integer, got: $MAX_LEN" >&2
    exit 1
fi
if ! [[ "$GPU_MEM" =~ ^[0-9]*\.?[0-9]+$ ]]; then
    echo "ERROR: GPU_MEMORY_UTILIZATION must be a number, got: $GPU_MEM" >&2
    exit 1
fi
if ! [[ "$MAX_SEQS" =~ ^[0-9]+$ ]]; then
    echo "ERROR: MAX_NUM_SEQS must be a positive integer, got: $MAX_SEQS" >&2
    exit 1
fi

ARGS=(--max-model-len "$MAX_LEN" --gpu-memory-utilization "$GPU_MEM" --max-num-seqs "$MAX_SEQS" --port 8000)

if [ -n "$QUANTIZATION" ]; then
    exec vllm serve /models/weights --quantization "$QUANTIZATION" "${ARGS[@]}"
else
    exec vllm serve /models/weights "${ARGS[@]}"
fi
