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
├── Dockerfile.baked                    # Multi-stage image (model weights baked in)
├── scripts/
│   ├── pre_start.sh                   # RunPod pre-start hook
│   └── post_start.sh                  # RunPod post-start hook
└── .github/workflows/
    ├── build-image.yml                # Build & push to GHCR (recommended)
    └── build-image-dockerhub.yml      # Build & push to Docker Hub (alternative)
```

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

Use `Dockerfile.baked` for zero-download boot. Change the model by setting the build arg:

```bash
docker buildx build --platform linux/amd64 \
  -f Dockerfile.baked \
  --build-arg MODEL=Qwen/Qwen2.5-Coder-7B-Instruct \
  -t youruser/vllm-coder-baked:latest \
  --push .
```

### Add dependencies

Add `RUN pip install ...` lines to the Dockerfile.

### GHCR visibility

GHCR images are private by default. To let RunPod pull the image:
- **Make public:** GitHub profile → Packages → select image → Package settings → Change visibility → Public
- **Or use private registry auth:** Configure credentials in RunPod Console → Container Registry

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
