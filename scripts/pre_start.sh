#!/bin/bash
# pre_start.sh — runs before SSH and Jupyter on RunPod base images
#
# Use this for setup tasks: install packages, download models, configure env.
# For the official vllm/vllm-openai image, this script is included in the image
# but only executes if the container uses RunPod's start.sh entrypoint.

echo "=== Pre-start: environment ready ==="

# Uncomment to download a model at boot (if not baked into the image):
# MODEL="${MODEL_NAME:-Qwen/Qwen2.5-Coder-32B-Instruct-AWQ}"
# echo "Downloading model: $MODEL"
# huggingface-cli download "$MODEL"
