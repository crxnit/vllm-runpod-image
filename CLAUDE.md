# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Docker image for serving LLMs with vLLM on RunPod GPU pods. Built for **linux/amd64** and deployed via GitHub Actions to GHCR. The default model is `Qwen/Qwen2.5-Coder-32B-Instruct-AWQ`.

## Two Dockerfile Variants

- **`Dockerfile`** — lightweight image (~5-10 GB), downloads model from HuggingFace at boot (~2 min cold start). Auto-built on push to main when Dockerfile/scripts change.
- **`Dockerfile.baked`** — large image (~25-30 GB) with model weights baked in for instant boot (~20 sec). Manual-trigger only via `gh workflow run build-image-baked.yml`. Uses a single-stage build designed for native amd64 runners — do not build locally on Apple Silicon.

Both extend `vllm/vllm-openai:latest` and install `huggingface_hub` and `tiktoken`.

## Build Commands

```bash
# Trigger standard image build (GHCR)
gh workflow run build-image.yml

# Trigger baked image build (default model)
gh workflow run build-image-baked.yml

# Trigger baked image build (custom model)
gh workflow run build-image-baked.yml -f model=Qwen/Qwen2.5-Coder-7B-Instruct

# Local build (from Mac, standard image only)
docker buildx create --name runpod-builder --use  # one-time
docker buildx build --platform linux/amd64 -t ghcr.io/<user>/vllm-runpod-image:latest --push .
```

## CI Workflows

- **`build-image.yml`** — auto-triggers on push to main (Dockerfile/scripts changes), pushes to GHCR with `latest` and SHA tags. Uses `GITHUB_TOKEN`.
- **`build-image-dockerhub.yml`** — alternative push to Docker Hub. Requires `DOCKER_USERNAME` and `DOCKER_PASSWORD` secrets.
- **`build-image-baked.yml`** — manual dispatch only, 60-min timeout, frees runner disk space before build. Accepts `model` input. Tags: `baked-latest`, `baked-<model-name>`, `baked-<sha>`.

## Key Architecture Details

- `scripts/pre_start.sh` and `scripts/post_start.sh` are RunPod lifecycle hooks — they run if the container uses RunPod's `start.sh` entrypoint. Currently minimal (logging only).
- The baked Dockerfile downloads weights via `huggingface_hub.snapshot_download()` Python API (not CLI) into `/models/weights`, then serves from that local path.
- The standard Dockerfile lets vLLM download the model by name at runtime via `CMD`.
- Model is served on port 8000 with `--quantization awq` and `--max-model-len 16384`.
- `VLLM_WORKER_MULTIPROC_METHOD=spawn` is set in both Dockerfiles for multi-GPU compatibility.
