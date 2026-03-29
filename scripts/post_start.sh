#!/bin/bash
# post_start.sh — runs after SSH and Jupyter are ready on RunPod base images
#
# Use this to start application services after the pod is fully initialized.
# For the official vllm/vllm-openai image, the CMD in the Dockerfile handles
# starting vLLM, so this script is typically not needed. It's here as a hook
# for custom workflows.

echo "=== Post-start: pod fully initialized ==="
