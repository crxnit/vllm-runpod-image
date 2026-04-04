#!/bin/bash
set -e

if [ -n "$QUANTIZATION" ]; then
    exec vllm serve /models/weights --quantization "$QUANTIZATION" --max-model-len 16384 --port 8000
else
    exec vllm serve /models/weights --max-model-len 16384 --port 8000
fi
