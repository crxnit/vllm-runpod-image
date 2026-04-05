# vLLM RunPod Image

Custom Docker image for serving coding LLMs with vLLM on RunPod GPU pods.

Built for **linux/amd64** — build via GitHub Actions or on an OCI x86 instance.

## Quick Start

1. Fork or clone this repo
2. Build the baked image: `gh workflow run build-image-baked.yml -f model=Qwen/Qwen2.5-Coder-3B-Instruct-AWQ -f tag=3b-coder-awq`
3. Create a RunPod template with image `ghcr.io/<your-username>/vllm-runpod-image:3b-coder-awq`, port `8000/http`, and env var `VLLM_API_KEY`
4. Launch a pod using the template

## Files

```
├── Dockerfile                          # Standard image (downloads model at boot)
├── Dockerfile.baked                    # Single-stage image (model weights baked in)
├── scripts/
│   ├── start.sh                       # Baked image entrypoint (vLLM serve)
│   ├── pre_start.sh                   # RunPod pre-start hook
│   └── post_start.sh                  # RunPod post-start hook
└── .github/workflows/
    ├── build-image.yml                # Build & push to GHCR (manual trigger)
    ├── build-image-baked.yml          # Build baked image & push to GHCR (manual trigger)
    └── build-image-dockerhub.yml      # Build & push to Docker Hub (manual trigger)
```

## Dockerfile vs Dockerfile.baked

**`Dockerfile`** downloads the model from HuggingFace at boot time every time the container starts. The image itself is small, but each cold start takes ~2 minutes while it pulls model weights.

**`Dockerfile.baked`** downloads the model at build time and bakes the weights into the image. The image is larger, but boots in ~20 seconds with zero download.

| | `Dockerfile` | `Dockerfile.baked` |
|---|---|---|
| Image size | ~5-10 GB | ~10-30 GB (depends on model) |
| Boot time | ~2 min (downloads model) | ~20 sec (model already loaded) |
| Build time | Fast | Slow (downloads model during build) |
| Change models | Edit CMD, rebuild small image | Edit build arg, rebuild large image |
| Registry cost | Minimal | Higher (storing larger image) |

Use `Dockerfile` if you're fine with a short wait on boot. Use `Dockerfile.baked` if you want instant startup and don't mind the larger image and registry costs.

**Important:** Both Dockerfiles are pinned to `vllm/vllm-openai:v0.11.2` (CUDA 12.8). Do not change to `latest` — it requires CUDA 12.9 which RunPod drivers don't currently support.

## Build Options

### GitHub Actions (all workflows are manual trigger only)

```bash
# Standard image
gh workflow run build-image.yml

# Baked image (default model: Qwen2.5-Coder-32B-AWQ)
gh workflow run build-image-baked.yml

# Baked image (custom AWQ model with custom tag)
gh workflow run build-image-baked.yml -f model=Qwen/Qwen2.5-Coder-3B-Instruct-AWQ -f tag=3b-coder-awq

# Baked image (non-quantized model)
gh workflow run build-image-baked.yml -f model=Qwen/Qwen2.5-Coder-7B-Instruct -f quantization="" -f tag=7b-instruct

# Baked image (gated model requiring HuggingFace token)
gh workflow run build-image-baked.yml -f model=meta-llama/Llama-3-8B -f hf_token=hf_xxx -f quantization="" -f tag=llama3-8b
```

GitHub Actions builds take ~20-30 minutes (cold cache, disk cleanup required).

### OCI x86 Instance (recommended for repeated builds)

Building on a persistent OCI x86 instance is much faster after the first build thanks to Docker layer caching (~5 seconds for cached rebuilds vs ~20-30 minutes on GitHub Actions).

```bash
# First time setup on OCI instance
sudo apt-get update && sudo apt-get install -y docker.io
sudo usermod -aG docker $USER
newgrp docker
docker login ghcr.io -u <your-username>  # use a PAT with write:packages scope
git clone https://github.com/<your-username>/vllm-runpod-image.git
cd vllm-runpod-image

# Build and push
docker build --platform linux/amd64 -f Dockerfile.baked \
  --build-arg MODEL=Qwen/Qwen2.5-Coder-3B-Instruct-AWQ \
  -t ghcr.io/<your-username>/vllm-runpod-image:3b-coder-awq \
  . && docker push ghcr.io/<your-username>/vllm-runpod-image:3b-coder-awq

# Subsequent builds (after code changes)
git pull
docker build ...  # same command — cached layers make this fast
```

## RunPod Deployment

### Create a template

```bash
runpodctl template create \
  --name "vLLM Qwen2.5 Coder 3B AWQ" \
  --image "ghcr.io/<your-username>/vllm-runpod-image:3b-coder-awq" \
  --container-disk-in-gb 20 \
  --ports "8000/http" \
  --env '{"VLLM_API_KEY":"your-api-key-here"}'
```

Generate an API key with `openssl rand -hex 32`.

### Recommended GPUs

| Model size | Recommended GPUs |
|---|---|
| 3B AWQ | RTX A4000, L4, RTX A5000 |
| 7B-14B AWQ | RTX A5000, RTX 4090 |
| 32B AWQ | A100, H100 |

### Notes

- Container disk: 20GB is sufficient for baked images
- No volume disk needed — model weights are baked into the image
- Pods without a network volume can only be terminated, not stopped
- The API is OpenAI-compatible on port 8000 (use `/v1/chat/completions`)

### Test the API

```bash
curl -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "/models/weights", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 100}' \
  https://<your-runpod-url>:8000/v1/chat/completions
```

## Customization

### Change the default model

For the standard image, edit the `CMD` line in `Dockerfile`. For baked images, pass `-f model=...` to the build workflow or `--build-arg MODEL=...` to Docker.

### Add dependencies

Add `RUN pip install ...` lines to the Dockerfile.

### GHCR visibility and cost

GHCR images are **private by default**. Storage cost depends on package visibility:

| Package Visibility | Storage | Bandwidth |
|---|---|---|
| **Public** | Free | Free |
| **Private** (free plan) | 500 MB included | 1 GB included |
| **Private** (overage) | $0.25/GB/mo | $0.50/GB/mo |

**Recommended:** Make the container package public — the repo stays private, but the image (which just contains vLLM + pip packages, no proprietary code) is publicly pullable at zero cost. RunPod can pull it without any registry auth configuration.

To make the package public after the first build:
GitHub profile > Packages > `vllm-runpod-image` > Package settings > Change visibility > Public

To keep everything private, configure RunPod to authenticate:
RunPod Console > Container Registry > add GHCR credentials (username + GitHub PAT with `read:packages` scope)
