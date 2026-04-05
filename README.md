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
├── cli/
│   ├── chat.py                        # Terminal chat interface
│   ├── loadtest.py                    # Concurrent load testing tool
│   ├── parse_logs.py                  # vLLM log parser (outputs CSV)
│   └── requirements.txt               # Python dependencies (openai, aiohttp)
├── ui/
│   └── index.html                     # Browser-based chat UI for testing
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
  --ports "8000/http,22/tcp" \
  --env '{"VLLM_API_KEY":"your-api-key-here"}'
```

Port `22/tcp` enables SSH access. Make sure your SSH public key is added in RunPod account settings.

Generate an API key with `openssl rand -hex 32`.

### Recommended GPUs

| Model size | Recommended GPUs | Container Disk |
|---|---|---|
| 3B AWQ | RTX A4000, L4, RTX A5000 | 20GB |
| 7B-14B AWQ | RTX A5000, RTX 4090 | 30GB |
| 32B AWQ | RTX A5000, RTX 4090 (24GB) | 40GB |
| 70B AWQ | A100 80GB | 80GB |

### Temperature Setting

Temperature controls the randomness of the model's output. Configurable in both the web UI and CLI (`/temp`).

| Value | Behavior | Use case |
|---|---|---|
| 0 | Deterministic, always picks the most likely token | Code generation, factual answers, reproducible output |
| 0.1-0.5 | Low randomness, mostly focused and predictable | Code evaluation, technical tasks |
| 0.7 (default) | Balanced randomness | General conversation |
| 1.0-1.5 | More creative and varied | Brainstorming, creative writing |
| 2.0 | Maximum randomness, often incoherent | Not recommended |

For testing code models, use **0.0-0.3** for consistent, correct output that reflects the model's actual capabilities.

### Notes

- Container disk: see GPU table above for sizing per model
- No volume disk needed — model weights are baked into the image
- Pods without a network volume can only be terminated, not stopped
- Port configuration (including SSH) is set at pod creation time — cannot be changed on a running pod
- The API is OpenAI-compatible on port 8000 (use `/v1/chat/completions`)

### Test the API

```bash
curl -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "/models/weights", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 100}' \
  https://<your-runpod-url>:8000/v1/chat/completions
```

## Test Interfaces

Two interfaces are included for testing deployed models.

### CLI Chat

```bash
pip install -r cli/requirements.txt
cd /path/to/your/project
python /path/to/cli/chat.py --endpoint https://your-pod-id-8000.proxy.runpod.net --key YOUR_API_KEY
```

Workspace-aware chat that automatically includes your CWD, git branch, and file listing in the model's context. Config is saved to `~/.config/vllm-chat/config.json` so you only set endpoint/key once.

**Chat commands:** `/help`, `/clear`, `/system`, `/temp`, `/max`, `/model`, `/history`, `/config`, `/auto`, `/quit`

**Workspace commands:**
- `/ls [path]` — list files
- `/tree [path]` — directory tree (3 levels)
- `/read <file>` — inject file contents into conversation
- `/write <file>` — write last response (extracts code blocks) to file
- `/diff [file]` — show git diff
- `/sh <command>` — run shell command (30s timeout)
- `/pwd` — show working directory

**Autonomous mode:** the model can read/write files and run commands on its own. Each action requires confirmation unless auto-approve is enabled (`/auto` or `--auto-approve`). Example: ask `create a Python script that reverses a string` and the model will write the file directly.

**Inline file references:** use `@filename.py` in your message to automatically attach file contents (e.g. `explain what @main.py does`).

### Load Test

```bash
python cli/loadtest.py --endpoint https://your-pod-id-8000.proxy.runpod.net --key YOUR_API_KEY
```

Sends concurrent requests at increasing concurrency levels (default: 1, 5, 10, 20) and reports:

| Metric | Description |
|---|---|
| Avg/P50/P99 | Response latency in seconds |
| TTFT | Time to first token |
| Tok/s | Total throughput across all concurrent requests |

Options:
- `--concurrency 1,5,10,20,30` — concurrency levels to test
- `--requests 10` — requests per concurrency level
- `--max-tokens 256` — max tokens per response
- `--temperature 0.3` — temperature

Uses the saved config from `cli/chat.py` if `--endpoint`/`--key` are not provided.

### Log Parser

Parse vLLM container logs (copy from RunPod dashboard) into CSV for analysis:

```bash
# From a saved log file
python cli/parse_logs.py runpod_logs.txt -o metrics.csv

# Paste logs directly (Ctrl+D when done)
python cli/parse_logs.py

# Also export HTTP request data
python cli/parse_logs.py runpod_logs.txt -o metrics.csv --requests-csv requests.csv
```

Extracts per-interval metrics: prompt/generation throughput (tok/s), running/waiting requests, GPU KV cache usage %, and prefix cache hit rate. Prints a summary with avg/max/min stats.

### Web UI

```bash
open ui/index.html
```

Enter your RunPod proxy URL and API key. Features: streaming responses, multi-turn conversation, configurable temperature/max tokens, settings persisted in localStorage.

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
