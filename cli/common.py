"""Shared utilities for CLI tools: config, ANSI colors, and helpers."""

import json
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "vllm-chat" / "config.json"

# ANSI colors
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
RESET = "\033[0m"


def load_config():
    """Load config from disk, returning empty dict on failure."""
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"{YELLOW}Warning: corrupt config file, starting fresh: {e}{RESET}")
            return {}
    return {}


def save_config(config):
    """Write config to disk with restricted permissions."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass
