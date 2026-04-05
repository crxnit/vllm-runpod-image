#!/usr/bin/env python3
"""Simple CLI chat interface for vLLM models on RunPod."""

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    print("Missing dependency. Install with: pip install openai")
    sys.exit(1)

CONFIG_PATH = Path.home() / ".config" / "vllm-chat" / "config.json"

# ANSI colors
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def print_help():
    print(f"""
{BOLD}Commands:{RESET}
  {CYAN}/help{RESET}           Show this help
  {CYAN}/clear{RESET}          Clear conversation history
  {CYAN}/config{RESET}         Show current configuration
  {CYAN}/endpoint URL{RESET}   Set the API endpoint
  {CYAN}/key KEY{RESET}        Set the API key
  {CYAN}/model NAME{RESET}     Set the model name
  {CYAN}/temp VALUE{RESET}     Set temperature (0-2)
  {CYAN}/max VALUE{RESET}      Set max tokens
  {CYAN}/system MSG{RESET}     Set system prompt
  {CYAN}/history{RESET}        Show conversation history
  {CYAN}/quit{RESET}           Exit

{DIM}Multi-line input: end with a blank line or Ctrl+D.
Press Ctrl+C to cancel a response.{RESET}
""")


def print_config(config):
    print(f"\n{BOLD}Configuration:{RESET}")
    print(f"  Endpoint:    {config.get('endpoint', DIM + 'not set' + RESET)}")
    print(f"  API Key:     {DIM}{'*' * 8 + config['key'][-4:] if config.get('key') else 'not set'}{RESET}")
    print(f"  Model:       {config.get('model', '/models/weights')}")
    print(f"  Temperature: {config.get('temperature', 0.7)}")
    print(f"  Max Tokens:  {config.get('max_tokens', 1024)}")
    print(f"  System:      {config.get('system', DIM + 'none' + RESET)}")
    print(f"  Config file: {DIM}{CONFIG_PATH}{RESET}")
    print()


def get_client(config):
    endpoint = config.get("endpoint", "").rstrip("/")
    if not endpoint:
        print(f"{RED}No endpoint set. Use /endpoint URL{RESET}")
        return None
    return OpenAI(
        base_url=endpoint + "/v1",
        api_key=config.get("key", "no-key"),
    )


def stream_response(client, messages, config):
    try:
        stream = client.chat.completions.create(
            model=config.get("model", "/models/weights"),
            messages=messages,
            max_tokens=config.get("max_tokens", 1024),
            temperature=config.get("temperature", 0.7),
            stream=True,
        )

        print(f"\n{GREEN}assistant{RESET}: ", end="", flush=True)
        full_response = ""

        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                print(delta, end="", flush=True)
                full_response += delta

        print("\n")
        return full_response

    except KeyboardInterrupt:
        print(f"\n{DIM}(cancelled){RESET}\n")
        return None
    except Exception as e:
        print(f"\n{RED}Error: {e}{RESET}\n")
        return None


def read_input():
    """Read user input, supporting multi-line with blank line termination."""
    lines = []
    try:
        first = input(f"{CYAN}you{RESET}: ")
    except EOFError:
        return None

    # Single line input (most common)
    if first.strip() != "":
        return first

    # Multi-line: first line was blank, keep reading
    print(f"{DIM}(multi-line mode, blank line to send){RESET}")
    try:
        while True:
            line = input(f"{DIM}...{RESET} ")
            if line.strip() == "":
                break
            lines.append(line)
    except EOFError:
        pass

    return "\n".join(lines) if lines else None


def main():
    parser = argparse.ArgumentParser(description="CLI chat for vLLM models")
    parser.add_argument("--endpoint", help="API endpoint URL")
    parser.add_argument("--key", help="API key")
    parser.add_argument("--model", help="Model name", default=None)
    parser.add_argument("--temperature", type=float, help="Temperature", default=None)
    parser.add_argument("--max-tokens", type=int, help="Max tokens", default=None)
    parser.add_argument("--system", help="System prompt", default=None)
    args = parser.parse_args()

    config = load_config()

    # CLI args override saved config
    if args.endpoint:
        config["endpoint"] = args.endpoint
    if args.key:
        config["key"] = args.key
    if args.model:
        config["model"] = args.model
    if args.temperature is not None:
        config["temperature"] = args.temperature
    if args.max_tokens is not None:
        config["max_tokens"] = args.max_tokens
    if args.system:
        config["system"] = args.system

    save_config(config)

    print(f"\n{BOLD}vLLM Chat{RESET} {DIM}(type /help for commands){RESET}")

    endpoint = config.get("endpoint", "")
    if endpoint:
        # Check connection
        client = get_client(config)
        if client:
            try:
                models = client.models.list()
                model_id = models.data[0].id if models.data else "unknown"
                print(f"{DIM}Connected to {endpoint}{RESET}")
                print(f"{DIM}Model: {model_id}{RESET}")
            except Exception as e:
                print(f"{YELLOW}Warning: could not connect to {endpoint}: {e}{RESET}")
    else:
        print(f"{YELLOW}No endpoint configured. Use /endpoint URL to set one.{RESET}")

    print()

    messages = []
    if config.get("system"):
        messages.append({"role": "system", "content": config["system"]})

    while True:
        try:
            user_input = read_input()
        except KeyboardInterrupt:
            print()
            continue

        if user_input is None:
            print(f"\n{DIM}Goodbye!{RESET}")
            break

        text = user_input.strip()
        if not text:
            continue

        # Handle commands
        if text.startswith("/"):
            parts = text.split(None, 1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if cmd in ("/quit", "/exit", "/q"):
                print(f"{DIM}Goodbye!{RESET}")
                break
            elif cmd == "/help":
                print_help()
            elif cmd == "/clear":
                messages = []
                if config.get("system"):
                    messages.append({"role": "system", "content": config["system"]})
                print(f"{DIM}Conversation cleared.{RESET}\n")
            elif cmd == "/config":
                print_config(config)
            elif cmd == "/endpoint":
                if arg:
                    config["endpoint"] = arg.rstrip("/")
                    save_config(config)
                    print(f"{DIM}Endpoint set to {config['endpoint']}{RESET}\n")
                else:
                    print(f"Endpoint: {config.get('endpoint', 'not set')}\n")
            elif cmd == "/key":
                if arg:
                    config["key"] = arg
                    save_config(config)
                    print(f"{DIM}API key updated.{RESET}\n")
                else:
                    print(f"API key: {'*' * 8 + config['key'][-4:] if config.get('key') else 'not set'}\n")
            elif cmd == "/model":
                if arg:
                    config["model"] = arg
                    save_config(config)
                    print(f"{DIM}Model set to {arg}{RESET}\n")
                else:
                    print(f"Model: {config.get('model', '/models/weights')}\n")
            elif cmd == "/temp":
                if arg:
                    try:
                        config["temperature"] = float(arg)
                        save_config(config)
                        print(f"{DIM}Temperature set to {config['temperature']}{RESET}\n")
                    except ValueError:
                        print(f"{RED}Invalid temperature value.{RESET}\n")
                else:
                    print(f"Temperature: {config.get('temperature', 0.7)}\n")
            elif cmd == "/max":
                if arg:
                    try:
                        config["max_tokens"] = int(arg)
                        save_config(config)
                        print(f"{DIM}Max tokens set to {config['max_tokens']}{RESET}\n")
                    except ValueError:
                        print(f"{RED}Invalid max tokens value.{RESET}\n")
                else:
                    print(f"Max tokens: {config.get('max_tokens', 1024)}\n")
            elif cmd == "/system":
                if arg:
                    config["system"] = arg
                    save_config(config)
                    # Update system message in history
                    messages = [m for m in messages if m["role"] != "system"]
                    messages.insert(0, {"role": "system", "content": arg})
                    print(f"{DIM}System prompt set.{RESET}\n")
                else:
                    print(f"System: {config.get('system', 'none')}\n")
            elif cmd == "/history":
                if not messages:
                    print(f"{DIM}No messages.{RESET}\n")
                else:
                    print()
                    for m in messages:
                        role_color = CYAN if m["role"] == "user" else GREEN if m["role"] == "assistant" else YELLOW
                        content_preview = m["content"][:100] + ("..." if len(m["content"]) > 100 else "")
                        print(f"  {role_color}{m['role']}{RESET}: {content_preview}")
                    print()
            else:
                print(f"{RED}Unknown command: {cmd}. Type /help for commands.{RESET}\n")
            continue

        # Send message
        client = get_client(config)
        if not client:
            continue

        messages.append({"role": "user", "content": text})
        response = stream_response(client, messages, config)

        if response:
            messages.append({"role": "assistant", "content": response})
        else:
            messages.pop()  # remove failed user message


if __name__ == "__main__":
    main()
