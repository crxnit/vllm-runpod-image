# vLLM RunPod Image

Custom Docker image for serving coding LLMs with vLLM on RunPod GPU pods.

Built for **linux/amd64** via GitHub Actions — no local cross-compilation needed on Apple Silicon.

## Quick Start

1. Fork or clone this repo
2. Push to GitHub
3. The GitHub Actions workflow builds and pushes the image to GHCR automatically
4. Use the image in your RunPod template: `ghcr.io/<your-username>/vllm-runpod-image:latest`

## Files

```
├── Dockerfile                          # Standard image (downloads model at boot)
├── Dockerfile.baked                    # Single-stage image (model weights baked in)
├── scripts/
│   ├── pre_start.sh                   # RunPod pre-start hook
│   └── post_start.sh                  # RunPod post-start hook
└── .github/workflows/
    ├── build-image.yml                # Build & push to GHCR (recommended)
    └── build-image-dockerhub.yml      # Build & push to Docker Hub (alternative)
```

## Dockerfile vs Dockerfile.baked

This repo includes two Dockerfiles for different use cases:

**`Dockerfile`** downloads the model from HuggingFace at boot time every time the container starts. The image itself is small, but each cold start takes ~2 minutes while it pulls model weights.

**`Dockerfile.baked`** downloads the model at build time and bakes the weights into the image. The image is large, but boots instantly with zero download. It uses a single-stage build — designed to run via **GitHub Actions** where the runner is native amd64 (no emulation). Building this locally on Apple Silicon is not recommended due to QEMU overhead.

| | `Dockerfile` | `Dockerfile.baked` |
|---|---|---|
| Image size | ~5-10 GB | ~25-30 GB |
| Boot time | ~2 min (downloads model) | ~20 sec (model already loaded) |
| Build time | Fast | Slow (downloads model during build) |
| Change models | Edit CMD, rebuild small image | Edit build arg, rebuild large image |
| Registry cost | Minimal | Higher (storing 25+ GB image) |

Use `Dockerfile` if you're fine with a short wait on boot. Use `Dockerfile.baked` if you want instant startup and don't mind the larger image and registry costs.

## Workflows

### GHCR (default)

Uses the built-in `GITHUB_TOKEN` — no extra secrets needed.

Image published to: `ghcr.io/<your-username>/vllm-runpod-image:latest`

### Docker Hub

Requires repository secrets:
- `DOCKER_USERNAME` — your Docker Hub username
- `DOCKER_PASSWORD` — a Docker Hub access token

## Customization

### Change the default model

Edit the `CMD` line in `Dockerfile`:

```dockerfile
CMD ["vllm", "serve", "your-model-name", \
     "--quantization", "awq", \
     "--max-model-len", "16384", \
     "--port", "8000"]
```

### Bake model weights into the image

Use `Dockerfile.baked` for zero-download boot. Build via GitHub Actions (recommended — native amd64, no emulation):

```bash
# Default model (Qwen2.5-Coder-32B-AWQ)
gh workflow run build-image-baked.yml

# Custom model
gh workflow run build-image-baked.yml -f model=Qwen/Qwen2.5-Coder-7B-Instruct
```

The baked workflow frees disk space on the runner before building to accommodate large model weights. The image is tagged as `baked-latest` and `baked-<model-name>`.

Note: building `Dockerfile.baked` locally on Apple Silicon is not recommended — downloading 20 GB of model weights under QEMU emulation is extremely slow. Use GitHub Actions instead.

### Add dependencies

Add `RUN pip install ...` lines to the Dockerfile.

### GHCR visibility and cost

GHCR images are **private by default**. Storage cost depends on package visibility:

| Package Visibility | Storage | Bandwidth |
|---|---|---|
| **Public** | Free | Free |
| **Private** (free plan) | 500 MB included | 1 GB included |
| **Private** (overage) | $0.25/GB/mo | $0.50/GB/mo |

A private `Dockerfile` image (~5-10 GB) costs roughly $1-2.50/mo. A private `Dockerfile.baked` image (~25-30 GB) costs $6-7.50/mo.

**Recommended:** Make the container package public — the repo stays private, but the image (which just contains vLLM + pip packages, no proprietary code) is publicly pullable at zero cost. RunPod can pull it without any registry auth configuration.

To make the package public after the first build:
GitHub profile → Packages → `vllm-runpod-image` → Package settings → Change visibility → Public

To keep everything private, configure RunPod to authenticate:
RunPod Console → Container Registry → add GHCR credentials (username + GitHub PAT with `read:packages` scope)

## Local build (from Mac)

```bash
# One-time setup
docker buildx create --name runpod-builder --use
docker buildx inspect --bootstrap

# Build and push
docker buildx build --platform linux/amd64 \
  -t ghcr.io/<your-username>/vllm-runpod-image:latest \
  --push .
```

## Manual workflow trigger

```bash
gh workflow run build-image.yml
```
