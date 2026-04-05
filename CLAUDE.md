# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Docker image for serving LLMs with vLLM on RunPod GPU pods. Built for **linux/amd64** and deployed to GHCR. Includes purpose-built web chat UIs (e.g. college admissions advisor) and CLI tools for testing. The recommended model for interactive chat is `Qwen/Qwen2.5-7B-Instruct-AWQ` on an RTX A5000.

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
- Model is served on port 8000. `--max-model-len` defaults to 16384 but is configurable via `MAX_MODEL_LEN` env var in the RunPod template (e.g. set to 4096 for 24GB GPUs with 32B models). The baked Dockerfile uses a `QUANTIZATION` build arg/env var (default `awq`) to conditionally pass `--quantization` — set to empty for non-quantized models.
- `VLLM_WORKER_MULTIPROC_METHOD=spawn` is set in both Dockerfiles for multi-GPU compatibility.
- Base image is pinned to `vllm/vllm-openai:v0.11.2` (CUDA 12.8). Do not use `latest` — it requires CUDA 12.9 which RunPod drivers don't support.

## Test Interfaces

- **CLI Chat** — `cli/chat.py` is a workspace-aware terminal chat interface (requires `openai` pip package). Config saved to `~/.config/vllm-chat/config.json`. Run from any project directory: `python cli/chat.py --endpoint URL --key KEY`. Automatically includes CWD, git branch, and file listing in system prompt. The model can autonomously read/write files and run commands using XML tags (`<write_file>`, `<read_file>`, `<run_command>`), with user confirmation for each action (toggle with `/auto`). Supports `/read`, `/write`, `/ls`, `/tree`, `/diff`, `/sh`, `/auto` workspace commands, `@file.py` inline file references, and standard chat commands. Streaming, multi-turn conversation, Ctrl+C to cancel.
- **Load Test** — `cli/loadtest.py` sends concurrent requests at increasing concurrency levels and reports throughput (tok/s), latency (avg/p50/p99), and TTFT. Run: `python cli/loadtest.py --endpoint URL --key KEY`. Uses 20 built-in coding prompts. Configurable via `--concurrency`, `--requests`, `--max-tokens`.
- **Log Parser** — `cli/parse_logs.py` parses vLLM container logs (from RunPod dashboard) and writes engine metrics to CSV. Run: `python cli/parse_logs.py logfile.txt -o metrics.csv`. Also accepts stdin. Extracts throughput, KV cache usage, running/waiting reqs, and prints a summary. No extra dependencies.
- **Web UI** — `ui/index.html` (developer) and purpose-built UIs like `ui/college-advisor.html`. All share `ui/shared/styles.css` and `ui/shared/chat.js`. New UIs only need an HTML file with a `CHAT_CONFIG` object defining `id`, `mode` (simple/developer), `systemPrompt`, `welcomeMessage`, `starters`, `maxTokens`, `temperature`, `stripThinking`. Theming via CSS variables. Settings persist in localStorage per config `id`.

## RunPod Deployment

- Create a RunPod template via `runpodctl template create` with the GHCR image, ports `8000/http,22/tcp`, and `VLLM_API_KEY` env var. Port 22 enables SSH access.
- Recommended for interactive chat: 7B AWQ on RTX A5000 ($0.27/hr) — best speed/cost balance. 3B/14B AWQ also fit on A4000/A5000. 32B AWQ needs `MAX_MODEL_LEN=4096` on 24GB GPUs. 70B AWQ needs A100 80GB with `MAX_MODEL_LEN=8192`. All images default `MAX_NUM_SEQS=64` to prevent warmup OOM.
- Container disk: 20GB for 3B/7B models, 30GB for 14B, 40GB for 32B, 80GB for 70B. No volume disk needed (weights are baked in).
- Pods without a network volume can only be terminated, not stopped.
- Port configuration is set at pod creation time — cannot be changed on a running pod.
