#!/bin/bash
set -e

MAX_LEN="${MAX_MODEL_LEN:-16384}"
GPU_MEM="${GPU_MEMORY_UTILIZATION:-0.95}"
MAX_SEQS="${MAX_NUM_SEQS:-64}"

ARGS=(--max-model-len "$MAX_LEN" --gpu-memory-utilization "$GPU_MEM" --max-num-seqs "$MAX_SEQS" --port 8000)

if [ -n "$QUANTIZATION" ]; then
    exec vllm serve /models/weights --quantization "$QUANTIZATION" "${ARGS[@]}"
else
    exec vllm serve /models/weights "${ARGS[@]}"
fi
