#!/bin/bash
# =============================================================================
# Build and push to Docker Hub (free public registry — no private registry needed).
#
# Usage:
#   ./build.sh <dockerhub-username> [tag]
#
# Examples:
#   ./build.sh myuser
#   ./build.sh myuser v1.0
#
# After pushing, paste the image name into RunPod:
#   New Pod → Container Image → <dockerhub-username>/vllm-lora:<tag>
#
# ---------------------------------------------------------------------------
# NO-REGISTRY RUNPOD OPTION (no build or push required)
# ---------------------------------------------------------------------------
# You can skip this script entirely and run on RunPod using the public base
# image directly. start.sh installs all Python dependencies at container
# startup, so no custom image is needed.
#
# In RunPod → New Pod:
#   Container Image : vllm/vllm-openai:latest
#   Container Disk  : 20 GB (or more)
#
# Upload start.sh to a RunPod Network Volume (or paste its path as the
# Docker Command override), then set environment variables including:
#   SKIP_PIP_INSTALL = false   (default — installs deps on first boot)
#   MODEL_NAME       = Qwen/Qwen2.5-7B-Instruct
#   HF_TOKEN         = <your HuggingFace token, if model is gated>
#   API_KEY          = <secret key for classify scripts>
#
# Subsequent starts are faster if you set SKIP_PIP_INSTALL=true once the
# packages are cached on a persistent network volume.
# =============================================================================
set -euo pipefail

DOCKERHUB_USER="${1:-}"
TAG="${2:-latest}"

if [[ -z "${DOCKERHUB_USER}" ]]; then
    echo "Error: Docker Hub username required."
    echo "Usage: ./build.sh <dockerhub-username> [tag]"
    exit 1
fi

IMAGE="${DOCKERHUB_USER}/vllm-lora:${TAG}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================================"
echo " Building : ${IMAGE}"
echo " Context  : ${SCRIPT_DIR}"
echo "============================================================"

docker build \
    --platform linux/amd64 \
    -t "${IMAGE}" \
    "${SCRIPT_DIR}"

echo ""
echo "============================================================"
echo " Pushing  : ${IMAGE}"
echo "============================================================"
echo " (If not logged in, run: docker login)"
echo ""

docker push "${IMAGE}"

echo ""
echo "============================================================"
echo " Done. Use this image in RunPod:"
echo "   ${IMAGE}"
echo ""
echo " Recommended RunPod environment variables:"
echo "   MODEL_NAME        = Qwen/Qwen2.5-7B-Instruct"
echo "   HF_TOKEN          = <your HuggingFace token, if model is gated>"
echo "   API_KEY           = <secret key your classify scripts will use>"
echo "   MAX_MODEL_LEN     = 4096"
echo "   GPU_MEMORY_UTIL   = 0.90"
echo "   ENABLE_LORA       = false"
echo "   SKIP_PIP_INSTALL  = true   # packages already baked into this image"
echo ""
echo " Then call from classify scripts:"
echo "   python classify.py --backend vllm \\"
echo "     --server-url https://<pod-id>-8000.proxy.runpod.net \\"
echo "     --api-key <your API_KEY> \\"
echo "     --input transactions.csv"
echo "============================================================"
