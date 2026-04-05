#!/bin/bash
set -e

MAX_LEN="${MAX_MODEL_LEN:-16384}"
GPU_MEM="${GPU_MEMORY_UTILIZATION:-0.95}"

if [ -n "$QUANTIZATION" ]; then
    exec vllm serve /models/weights --quantization "$QUANTIZATION" --max-model-len "$MAX_LEN" --gpu-memory-utilization "$GPU_MEM" --port 8000
else
    exec vllm serve /models/weights --max-model-len "$MAX_LEN" --gpu-memory-utilization "$GPU_MEM" --port 8000
fi
