# Andrew Fix Plan

## Guiding Principle: Reuse `scripts/start.sh` instead of `vllm-lora/start.sh`

You already have a battle-tested `scripts/start.sh` used by `Dockerfile.baked` that validates inputs, serves from `/models/weights`, and uses `exec`. The `vllm-lora/start.sh` is a parallel, weaker copy. The DRY move is to **converge on a single start script** rather than maintaining two.

---

## 1. `Dockerfile.lora` — Bake the model + enable LoRA

**Pin the base image.** `vllm/vllm-openai:latest` is a moving target. `Dockerfile.baked` already pins `v0.11.2`. Use the same version for reproducibility.

**Add `ARG MODEL` and download weights at build time**, exactly like `Dockerfile.baked` lines 20-25. This gives you:
- Zero-download boot on RunPod (cold start goes from minutes to seconds)
- The model choice is a build-time variable, settable from the workflow

**Add `ARG HF_TOKEN`** for gated models, same pattern as `Dockerfile.baked` line 21-22.

**Drop `vllm-lora/start.sh`, use `scripts/start.sh` instead.** Extend `scripts/start.sh` to handle LoRA (see section 3 below) so both Dockerfiles share one entrypoint.

**Add `ENTRYPOINT ["/start.sh"]`** — this is the fix for the current "no model, full VRAM" bug and matches `Dockerfile.baked`.

**Separate runtime deps from training deps.** The training packages (`transformers`, `peft`, `trl`, `datasets`, `accelerate`, `bitsandbytes`) bloat the serving image. Consider splitting into two pip install layers — one for serving (small, cached) and one for training (only if you actually train inside this container). Or better: if training happens in a separate workflow, remove them entirely.

### Proposed structure

```dockerfile
FROM vllm/vllm-openai:v0.11.2

# --- shared base (keep in sync with Dockerfile.baked) ---
RUN pip install --no-cache-dir "huggingface_hub[cli,hf_xet]" tiktoken

ENV HF_HOME=/root/.cache/huggingface
ENV VLLM_WORKER_MULTIPROC_METHOD=spawn

# --- bake model weights ---
ARG MODEL=Qwen/Qwen2.5-7B-Instruct
ARG HF_TOKEN=""
RUN if [ -n "$HF_TOKEN" ]; then export HF_TOKEN="$HF_TOKEN"; fi && \
    python3 -c "..." "${MODEL}"        # same snippet as Dockerfile.baked

# --- LoRA/classification deps (only what serving needs) ---
RUN pip install --no-cache-dir requests pandas

# --- shared entrypoint ---
COPY scripts/start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 8000

ARG QUANTIZATION=""
ENV QUANTIZATION=${QUANTIZATION}
ENV ENABLE_LORA=true

ENTRYPOINT ["/start.sh"]
CMD []
```

---

## 2. `build-image-andrew.yml` — Pass model + token as build args

**Add workflow inputs** for `model` and `hf_token` alongside the existing `tag`:

```yaml
inputs:
  tag:
    description: "Image tag"
    default: "latest"
  model:
    description: "HuggingFace model ID to bake in"
    default: "Qwen/Qwen2.5-7B-Instruct"
  quantization:
    description: "Quantization method (awq, gptq, or empty)"
    default: ""
```

**Pass build args** to the `docker/build-push-action` step:

```yaml
build-args: |
  MODEL=${{ github.event.inputs.model }}
  HF_TOKEN=${{ secrets.HF_TOKEN }}
  QUANTIZATION=${{ github.event.inputs.quantization }}
```

Note: `HF_TOKEN` should come from **repository secrets**, not a workflow input — you never want tokens in the dispatch payload or logs. The `ARG` value doesn't persist in the final image layer metadata if you don't `ENV` it.

**Increase `timeout-minutes`** from 30 to 60+. Downloading a 7B model during build adds significant time, and a 32B model even more.

**Encode the model in the image tag** for traceability. Instead of just `latest`, consider a tag like `qwen2.5-7b-lora-latest` so you can tell what's baked in from the registry listing alone.

---

## 3. `scripts/start.sh` — Extend to support LoRA (single shared script)

The current `scripts/start.sh` is clean but LoRA-unaware. Add LoRA support so both `Dockerfile.baked` and `Dockerfile.lora` can use the same entrypoint:

```bash
# After existing ARGS array construction...

# LoRA support (opt-in via ENABLE_LORA=true)
if [ "${ENABLE_LORA:-false}" = "true" ]; then
    ARGS+=(--enable-lora)
    if [ -n "${LORA_MODULES:-}" ]; then
        ARGS+=(--lora-modules "$LORA_MODULES")
    fi
fi
```

This keeps the existing behavior for `Dockerfile.baked` (where `ENABLE_LORA` is unset, so LoRA stays off) while activating it for `Dockerfile.lora` (where the Dockerfile sets `ENV ENABLE_LORA=true`). The `LORA_MODULES` value can be supplied at runtime via RunPod env vars, e.g.:

```
LORA_MODULES=adapter=org/my-lora-adapter
```

**Also add:** input validation for `LORA_MODULES` matching the same defensive pattern as the existing numeric checks — a regex like `^[A-Za-z0-9._:/-]+$` to prevent injection.

---

## 4. Delete `vllm-lora/start.sh`

Once `scripts/start.sh` handles LoRA, this file is dead code. Remove it and the `vllm-lora/` directory (if nothing else lives there) to avoid confusion about which script is authoritative.

---

## Summary of changes by file

| File | What changes | Why |
|---|---|---|
| `Dockerfile.lora` | Pin base image, add `ARG MODEL`/`HF_TOKEN`, download weights at build, switch to `scripts/start.sh`, add `ENTRYPOINT`, set `ENV ENABLE_LORA=true` | Bakes model, enables LoRA, boots correctly |
| `build-image-andrew.yml` | Add `model`/`quantization` inputs, pass `build-args`, use `secrets.HF_TOKEN`, bump timeout | Makes model configurable per build |
| `scripts/start.sh` | Add `ENABLE_LORA` / `LORA_MODULES` block with validation | Single entrypoint for all Dockerfiles |
| `vllm-lora/start.sh` | Delete | Replaced by shared script |

The net effect: one start script, model baked at build time, LoRA enabled by default in the lora image, and the container boots straight into a serving-ready vLLM on RunPod.
