#!/usr/bin/env python3
"""CLI chat interface for vLLM models on RunPod with workspace awareness."""

import argparse
import fnmatch
import json
import os
import subprocess
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
MAGENTA = "\033[35m"
RESET = "\033[0m"

# Files/dirs to ignore when listing
IGNORE_PATTERNS = [
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".env", "*.pyc", ".DS_Store", ".idea", ".vscode",
]

CWD = Path.cwd()


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def should_ignore(name):
    """Check if a file/dir should be ignored in listings."""
    for pattern in IGNORE_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def get_tree(directory, prefix="", max_depth=3, current_depth=0):
    """Generate a tree view of a directory."""
    if current_depth >= max_depth:
        return ""

    lines = []
    try:
        entries = sorted(directory.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return ""

    entries = [e for e in entries if not should_ignore(e.name)]

    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        if entry.is_dir():
            lines.append(f"{prefix}{connector}{entry.name}/")
            extension = "    " if is_last else "│   "
            lines.append(get_tree(entry, prefix + extension, max_depth, current_depth + 1))
        else:
            size = entry.stat().st_size
            size_str = format_size(size)
            lines.append(f"{prefix}{connector}{entry.name}  {DIM}({size_str}){RESET}")

    return "\n".join(line for line in lines if line)


def format_size(size):
    """Format file size in human-readable form."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def resolve_path(path_str):
    """Resolve a path relative to CWD."""
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        p = CWD / p
    return p.resolve()


def read_file(path_str):
    """Read a file and return its contents with metadata."""
    path = resolve_path(path_str)
    if not path.exists():
        return None, f"File not found: {path}"
    if not path.is_file():
        return None, f"Not a file: {path}"
    try:
        size = path.stat().st_size
        if size > 100_000:
            return None, f"File too large ({format_size(size)}). Max 100KB."
        content = path.read_text(errors="replace")
        rel = path.relative_to(CWD) if path.is_relative_to(CWD) else path
        return content, str(rel)
    except Exception as e:
        return None, f"Error reading {path}: {e}"


def write_file(path_str, content):
    """Write content to a file."""
    path = resolve_path(path_str)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        rel = path.relative_to(CWD) if path.is_relative_to(CWD) else path
        return True, str(rel)
    except Exception as e:
        return False, f"Error writing {path}: {e}"


def run_shell(cmd):
    """Run a shell command and return output."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=30, cwd=str(CWD)
        )
        output = result.stdout
        if result.stderr:
            output += result.stderr
        return output.strip() if output.strip() else "(no output)"
    except subprocess.TimeoutExpired:
        return "(command timed out after 30s)"
    except Exception as e:
        return f"(error: {e})"


def build_system_prompt(config):
    """Build system prompt with workspace context."""
    parts = []

    # User-defined system prompt
    user_system = config.get("system", "")
    if user_system:
        parts.append(user_system)

    # Workspace context
    parts.append(f"Working directory: {CWD}")

    # Check for git repo
    git_dir = CWD / ".git"
    if git_dir.exists():
        branch = run_shell("git branch --show-current")
        parts.append(f"Git branch: {branch}")

    # Top-level file listing
    try:
        entries = sorted(CWD.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        entries = [e for e in entries if not should_ignore(e.name)]
        listing = ", ".join(
            f"{e.name}/" if e.is_dir() else e.name for e in entries[:30]
        )
        if len(entries) > 30:
            listing += f", ... ({len(entries) - 30} more)"
        parts.append(f"Files: {listing}")
    except Exception:
        pass

    parts.append(
        "You have access to the user's working directory. "
        "When the user asks about files, references code, or asks you to read/write files, "
        "work with the files in this directory. "
        "When writing code, be precise and match the existing style."
    )

    return "\n\n".join(parts)


def inject_file_context(text):
    """Check if user message references files with @file syntax and inject contents."""
    if "@" not in text:
        return text, []

    import re
    files_added = []
    # Match @path/to/file patterns
    pattern = re.compile(r"@([\w./\-_]+\.\w+)")

    for match in pattern.finditer(text):
        filepath = match.group(1)
        content, info = read_file(filepath)
        if content:
            files_added.append((filepath, content))

    if not files_added:
        return text, []

    # Build context with file contents
    context_parts = [text, "", "---", "Referenced files:", ""]
    for filepath, content in files_added:
        context_parts.append(f"### {filepath}")
        context_parts.append(f"```\n{content}\n```")
        context_parts.append("")

    return "\n".join(context_parts), files_added


def print_help():
    print(f"""
{BOLD}Chat Commands:{RESET}
  {CYAN}/help{RESET}              Show this help
  {CYAN}/clear{RESET}             Clear conversation history
  {CYAN}/config{RESET}            Show current configuration
  {CYAN}/endpoint URL{RESET}      Set the API endpoint
  {CYAN}/key KEY{RESET}           Set the API key
  {CYAN}/model NAME{RESET}        Set the model name
  {CYAN}/temp VALUE{RESET}        Set temperature (0-2)
  {CYAN}/max VALUE{RESET}         Set max tokens
  {CYAN}/system MSG{RESET}        Set system prompt
  {CYAN}/history{RESET}           Show conversation history
  {CYAN}/quit{RESET}              Exit

{BOLD}Workspace Commands:{RESET}
  {CYAN}/ls [path]{RESET}         List files in directory
  {CYAN}/tree [path]{RESET}       Show directory tree (3 levels)
  {CYAN}/read <file>{RESET}       Read file into conversation
  {CYAN}/write <file>{RESET}      Write last assistant response to file
  {CYAN}/diff <file>{RESET}       Show git diff for a file
  {CYAN}/sh <command>{RESET}      Run a shell command (30s timeout)
  {CYAN}/pwd{RESET}               Show working directory

{BOLD}Inline File References:{RESET}
  Use {CYAN}@filename.py{RESET} in your message to automatically include file contents.
  Example: "explain what @main.py does"

{DIM}Multi-line input: start with a blank line, end with a blank line.
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
    print(f"\n{BOLD}Workspace:{RESET}")
    print(f"  Directory:   {CWD}")
    git_dir = CWD / ".git"
    if git_dir.exists():
        branch = run_shell("git branch --show-current")
        print(f"  Git branch:  {branch}")
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
    print(f"{DIM}Workspace: {CWD}{RESET}")

    endpoint = config.get("endpoint", "")
    if endpoint:
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

    # Build initial system prompt with workspace context
    messages = []
    system_prompt = build_system_prompt(config)
    messages.append({"role": "system", "content": system_prompt})

    last_response = None

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
                messages = [{"role": "system", "content": build_system_prompt(config)}]
                last_response = None
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
                    # Rebuild system prompt with workspace context
                    messages = [m for m in messages if m["role"] != "system"]
                    messages.insert(0, {"role": "system", "content": build_system_prompt(config)})
                    print(f"{DIM}System prompt set.{RESET}\n")
                else:
                    print(f"System: {config.get('system', 'none')}\n")
            elif cmd == "/history":
                if len(messages) <= 1:  # only system message
                    print(f"{DIM}No messages.{RESET}\n")
                else:
                    print()
                    for m in messages:
                        if m["role"] == "system":
                            continue
                        role_color = CYAN if m["role"] == "user" else GREEN
                        content_preview = m["content"][:100] + ("..." if len(m["content"]) > 100 else "")
                        print(f"  {role_color}{m['role']}{RESET}: {content_preview}")
                    print()

            # Workspace commands
            elif cmd == "/pwd":
                print(f"  {CWD}\n")
            elif cmd == "/ls":
                target = resolve_path(arg) if arg else CWD
                if not target.is_dir():
                    print(f"{RED}Not a directory: {target}{RESET}\n")
                else:
                    try:
                        entries = sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
                        entries = [e for e in entries if not should_ignore(e.name)]
                        print()
                        for e in entries:
                            if e.is_dir():
                                print(f"  {CYAN}{e.name}/{RESET}")
                            else:
                                size = format_size(e.stat().st_size)
                                print(f"  {e.name}  {DIM}({size}){RESET}")
                        print()
                    except Exception as ex:
                        print(f"{RED}Error: {ex}{RESET}\n")
            elif cmd == "/tree":
                target = resolve_path(arg) if arg else CWD
                if not target.is_dir():
                    print(f"{RED}Not a directory: {target}{RESET}\n")
                else:
                    print(f"\n  {BOLD}{target.name}/{RESET}")
                    tree = get_tree(target)
                    for line in tree.split("\n"):
                        if line:
                            print(f"  {line}")
                    print()
            elif cmd == "/read":
                if not arg:
                    print(f"{RED}Usage: /read <file>{RESET}\n")
                else:
                    content, info = read_file(arg)
                    if content is None:
                        print(f"{RED}{info}{RESET}\n")
                    else:
                        lines = content.count("\n") + 1
                        print(f"{DIM}Read {info} ({lines} lines) into conversation.{RESET}\n")
                        file_msg = f"Contents of {info}:\n```\n{content}\n```"
                        messages.append({"role": "user", "content": file_msg})
            elif cmd == "/write":
                if not arg:
                    print(f"{RED}Usage: /write <file>{RESET}\n")
                elif not last_response:
                    print(f"{RED}No assistant response to write.{RESET}\n")
                else:
                    # Extract code blocks if present, otherwise write full response
                    import re
                    code_blocks = re.findall(r"```(?:\w*\n)?(.*?)```", last_response, re.DOTALL)
                    content = code_blocks[0].strip() if code_blocks else last_response

                    ok, info = write_file(arg, content + "\n")
                    if ok:
                        print(f"{DIM}Wrote to {info}{RESET}\n")
                    else:
                        print(f"{RED}{info}{RESET}\n")
            elif cmd == "/diff":
                if not arg:
                    output = run_shell("git diff")
                else:
                    path = resolve_path(arg)
                    output = run_shell(f"git diff -- {path}")
                print(f"\n{output}\n")
            elif cmd == "/sh":
                if not arg:
                    print(f"{RED}Usage: /sh <command>{RESET}\n")
                else:
                    output = run_shell(arg)
                    print(f"\n{output}\n")
            else:
                print(f"{RED}Unknown command: {cmd}. Type /help for commands.{RESET}\n")
            continue

        # Process @file references
        text, files_added = inject_file_context(text)
        if files_added:
            filenames = ", ".join(f[0] for f in files_added)
            print(f"{DIM}Attached: {filenames}{RESET}")

        # Send message
        client = get_client(config)
        if not client:
            continue

        messages.append({"role": "user", "content": text})
        response = stream_response(client, messages, config)

        if response:
            messages.append({"role": "assistant", "content": response})
            last_response = response
        else:
            messages.pop()  # remove failed user message


if __name__ == "__main__":
    main()
