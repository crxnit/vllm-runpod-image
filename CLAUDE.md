# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Docker image for serving LLMs with vLLM on RunPod GPU pods. Built for **linux/amd64** and deployed to GHCR. The default model is `Qwen/Qwen2.5-Coder-32B-Instruct-AWQ`.

## Two Dockerfile Variants

- **`Dockerfile`** — lightweight image (~5-10 GB), downloads model from HuggingFace at boot (~2 min cold start). Manual-trigger only via `gh workflow run build-image.yml`.
- **`Dockerfile.baked`** — large image with model weights baked in for instant boot (~20 sec). Manual-trigger only via `gh workflow run build-image-baked.yml`. Can also be built on an OCI x86 instance for faster cached rebuilds.

Both extend `vllm/vllm-openai:v0.11.2` (pinned to CUDA 12.8 for RunPod driver compatibility) and install `huggingface_hub` and `tiktoken`.

## Build Commands

```bash
# Trigger standard image build (GHCR, manual only)
gh workflow run build-image.yml

# Trigger baked image build (default model)
gh workflow run build-image-baked.yml

# Trigger baked image build (custom AWQ model with custom tag)
gh workflow run build-image-baked.yml -f model=Qwen/Qwen2.5-Coder-7B-Instruct-AWQ -f tag=7b-awq

# Trigger baked image build (non-quantized model)
gh workflow run build-image-baked.yml -f model=Qwen/Qwen2.5-Coder-7B-Instruct -f quantization="" -f tag=7b-instruct

# Trigger baked image build (gated model)
gh workflow run build-image-baked.yml -f model=meta-llama/Llama-3-8B -f hf_token=hf_xxx -f quantization="" -f tag=llama3-8b

# Build on OCI x86 instance (much faster with cached layers)
git pull
docker build --platform linux/amd64 -f Dockerfile.baked \
  --build-arg MODEL=Qwen/Qwen2.5-Coder-3B-Instruct-AWQ \
  -t ghcr.io/crxnit/vllm-runpod-image:3b-coder-awq \
  . && docker push ghcr.io/crxnit/vllm-runpod-image:3b-coder-awq
```

## CI Workflows

All three workflows are **manual dispatch only** (`workflow_dispatch`):

- **`build-image.yml`** — builds standard Dockerfile, pushes to GHCR with `latest` and SHA tags. Uses `GITHUB_TOKEN`.
- **`build-image-dockerhub.yml`** — alternative push to Docker Hub. Requires `DOCKER_USERNAME` and `DOCKER_PASSWORD` secrets (not currently configured).
- **`build-image-baked.yml`** — builds baked Dockerfile, 60-min timeout, frees runner disk space before build. Inputs: `model`, `quantization` (default `awq`, empty for non-quantized), `tag` (default `baked-latest`), `hf_token` (for gated models). Tags: `<tag>`, `baked-<model-name>`, `baked-<sha>`.

## Key Architecture Details

- The baked Dockerfile uses `ENTRYPOINT ["/start.sh"]` to override the base image's entrypoint. The `scripts/start.sh` script handles the conditional `--quantization` flag and exec's `vllm serve`.
- `scripts/pre_start.sh` and `scripts/post_start.sh` are RunPod lifecycle hooks. Currently minimal (logging only).
- The baked Dockerfile downloads weights via `huggingface_hub.snapshot_download()` Python API (not CLI) into `/models/weights`, then serves from that local path.
- The standard Dockerfile lets vLLM download the model by name at runtime via `CMD`.
- Model is served on port 8000 with `--max-model-len 16384`. The baked Dockerfile uses a `QUANTIZATION` build arg/env var (default `awq`) to conditionally pass `--quantization` — set to empty for non-quantized models.
- `VLLM_WORKER_MULTIPROC_METHOD=spawn` is set in both Dockerfiles for multi-GPU compatibility.
- Base image is pinned to `vllm/vllm-openai:v0.11.2` (CUDA 12.8). Do not use `latest` — it requires CUDA 12.9 which RunPod drivers don't support.

## Test Interfaces

- **CLI Chat** — `cli/chat.py` is a terminal chat interface (requires `openai` pip package). Config saved to `~/.config/vllm-chat/config.json`. Run: `python cli/chat.py --endpoint URL --key KEY`. Supports slash commands (`/help`, `/clear`, `/system`, `/temp`, `/max`, `/model`, `/history`), streaming, multi-turn conversation, and Ctrl+C to cancel.
- **Load Test** — `cli/loadtest.py` sends concurrent requests at increasing concurrency levels and reports throughput (tok/s), latency (avg/p50/p99), and TTFT. Run: `python cli/loadtest.py --endpoint URL --key KEY`. Uses 20 built-in coding prompts. Configurable via `--concurrency`, `--requests`, `--max-tokens`.
- **Log Parser** — `cli/parse_logs.py` parses vLLM container logs (from RunPod dashboard) and writes engine metrics to CSV. Run: `python cli/parse_logs.py logfile.txt -o metrics.csv`. Also accepts stdin. Extracts throughput, KV cache usage, running/waiting reqs, and prints a summary. No extra dependencies.
- **Web UI** — `ui/index.html` is a single-file browser-based chat UI. Open directly (`open ui/index.html`), enter the RunPod proxy URL and API key. Supports streaming responses, multi-turn conversation, configurable temperature/max tokens. Settings persist in localStorage.

## RunPod Deployment

- Create a RunPod template via `runpodctl template create` with the GHCR image, port 8000/http, and `VLLM_API_KEY` env var.
- Recommended GPU for 3B AWQ models: RTX A4000, L4, or RTX A5000. Larger models (14B, 32B) need A100/H100.
- Container disk: 20GB. No volume disk needed (weights are baked in).
- Pods without a network volume can only be terminated, not stopped.
