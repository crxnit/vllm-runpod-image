FROM vllm/vllm-openai:v0.6.3

# Install additional dependencies
RUN pip install --no-cache-dir huggingface_hub tiktoken

# Set environment
ENV HF_HOME=/root/.cache/huggingface
ENV VLLM_WORKER_MULTIPROC_METHOD=spawn

# Copy startup scripts (for RunPod base images that support hooks)
COPY scripts/pre_start.sh /pre_start.sh
COPY scripts/post_start.sh /post_start.sh
RUN chmod +x /pre_start.sh /post_start.sh

EXPOSE 8000

# Default: serve Qwen2.5-Coder-32B AWQ
# Override via RunPod template startup command or environment variables
CMD ["vllm", "serve", "Qwen/Qwen2.5-Coder-32B-Instruct-AWQ", \
     "--quantization", "awq", \
     "--max-model-len", "16384", \
     "--port", "8000"]
