#!/usr/bin/env python3
"""CLI chat interface for vLLM models on RunPod with workspace awareness."""

import argparse
import fnmatch
import json
import os
import re
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

# Patterns for parsing autonomous actions from model responses
WRITE_FILE_PATTERN = re.compile(
    r"<write_file\s+path=[\"']([^\"']+)[\"']\s*>(.*?)</write_file>",
    re.DOTALL,
)
RUN_CMD_PATTERN = re.compile(
    r"<run_command>(.*?)</run_command>",
    re.DOTALL,
)
READ_FILE_PATTERN = re.compile(
    r"<read_file\s+path=[\"']([^\"']+)[\"']\s*/?>",
)

AGENT_INSTRUCTIONS = """\
You can perform actions in the user's workspace using these tags:

To write or create a file:
<write_file path="relative/path.py">
file contents here
</write_file>

To read a file into context:
<read_file path="relative/path.py"/>

To run a shell command:
<run_command>command here</run_command>

Rules:
- Always use these tags when the user asks you to create, edit, or write files.
- Always use these tags when you need to read a file to answer a question.
- Always use these tags when the user asks you to run a command.
- Write complete file contents, not partial snippets.
- The user will be asked to approve each action before it executes.
- You can include multiple actions in a single response.
- Prefer relative paths from the working directory.
"""


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def should_ignore(name):
    for pattern in IGNORE_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def get_tree(directory, prefix="", max_depth=3, current_depth=0):
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
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def resolve_path(path_str):
    p = Path(path_str).expanduser()
    if not p.is_absolute():
        p = CWD / p
    return p.resolve()


def read_file(path_str):
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
    path = resolve_path(path_str)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        rel = path.relative_to(CWD) if path.is_relative_to(CWD) else path
        return True, str(rel)
    except Exception as e:
        return False, f"Error writing {path}: {e}"


def run_shell(cmd):
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


def flush_stdin():
    """Drain any buffered input from stdin."""
    try:
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except (ImportError, termios.error):
        pass


def confirm(prompt):
    """Ask user for Y/n confirmation."""
    try:
        flush_stdin()
        answer = input(f"{YELLOW}{prompt} [Y/n]{RESET} ").strip().lower()
        # Default to yes on empty input
        if answer == "":
            return True
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def process_actions(response, messages, auto_approve=False):
    """Parse and execute autonomous actions from model response."""
    actions_taken = []

    # Process file writes
    for match in WRITE_FILE_PATTERN.finditer(response):
        filepath = match.group(1)
        content = match.group(2).strip()

        # Show preview
        lines = content.count("\n") + 1
        print(f"{MAGENTA}Action: write {filepath} ({lines} lines){RESET}")

        # Show first/last few lines
        content_lines = content.split("\n")
        if len(content_lines) <= 10:
            for line in content_lines:
                print(f"  {DIM}{line}{RESET}")
        else:
            for line in content_lines[:5]:
                print(f"  {DIM}{line}{RESET}")
            print(f"  {DIM}... ({len(content_lines) - 10} more lines) ...{RESET}")
            for line in content_lines[-5:]:
                print(f"  {DIM}{line}{RESET}")

        if auto_approve or confirm("Write this file?"):
            ok, info = write_file(filepath, content + "\n")
            if ok:
                print(f"  {GREEN}Wrote {info}{RESET}\n")
                actions_taken.append(f"Wrote {info}")
            else:
                print(f"  {RED}{info}{RESET}\n")
                actions_taken.append(f"Failed to write {filepath}: {info}")
        else:
            print(f"  {DIM}Skipped.{RESET}\n")
            actions_taken.append(f"User skipped writing {filepath}")

    # Process file reads
    for match in READ_FILE_PATTERN.finditer(response):
        filepath = match.group(1)
        print(f"{MAGENTA}Action: read {filepath}{RESET}")

        content, info = read_file(filepath)
        if content:
            lines = content.count("\n") + 1
            print(f"  {DIM}Read {info} ({lines} lines){RESET}\n")
            file_msg = f"Contents of {info}:\n```\n{content}\n```"
            messages.append({"role": "user", "content": file_msg})
            actions_taken.append(f"Read {info}")
        else:
            print(f"  {RED}{info}{RESET}\n")
            actions_taken.append(f"Failed to read {filepath}: {info}")

    # Process shell commands
    for match in RUN_CMD_PATTERN.finditer(response):
        cmd = match.group(1).strip()
        print(f"{MAGENTA}Action: run `{cmd}`{RESET}")

        if auto_approve or confirm("Run this command?"):
            output = run_shell(cmd)
            print(f"  {DIM}{output}{RESET}\n")
            # Feed output back to conversation
            messages.append({"role": "user", "content": f"Command output for `{cmd}`:\n```\n{output}\n```"})
            actions_taken.append(f"Ran `{cmd}`")
        else:
            print(f"  {DIM}Skipped.{RESET}\n")
            actions_taken.append(f"User skipped running `{cmd}`")

    return actions_taken


def build_system_prompt(config):
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

    # Agent instructions
    parts.append(AGENT_INSTRUCTIONS)

    return "\n\n".join(parts)


def inject_file_context(text):
    if "@" not in text:
        return text, []

    files_added = []
    pattern = re.compile(r"@([\w./\-_]+\.\w+)")

    for match in pattern.finditer(text):
        filepath = match.group(1)
        content, info = read_file(filepath)
        if content:
            files_added.append((filepath, content))

    if not files_added:
        return text, []

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
  {CYAN}/auto{RESET}              Toggle auto-approve mode (skip confirmations)
  {CYAN}/quit{RESET}              Exit

{BOLD}Workspace Commands:{RESET}
  {CYAN}/ls [path]{RESET}         List files in directory
  {CYAN}/tree [path]{RESET}       Show directory tree (3 levels)
  {CYAN}/read <file>{RESET}       Read file into conversation
  {CYAN}/write <file>{RESET}      Write last assistant response to file
  {CYAN}/diff <file>{RESET}       Show git diff for a file
  {CYAN}/sh <command>{RESET}      Run a shell command (30s timeout)
  {CYAN}/pwd{RESET}               Show working directory

{BOLD}Autonomous Mode:{RESET}
  The model can autonomously read/write files and run commands.
  Each action requires your approval (unless {CYAN}/auto{RESET} is enabled).
  Example: "create a Python script that reverses a string"

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
    print(f"  Auto-approve:{YELLOW} {'ON' if config.get('auto_approve') else 'OFF'}{RESET}")
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
    lines = []
    try:
        first = input(f"{CYAN}you{RESET}: ")
    except EOFError:
        return None

    if first.strip() != "":
        return first

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
    parser.add_argument("--auto-approve", action="store_true", help="Skip action confirmations")
    args = parser.parse_args()

    config = load_config()

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
    if args.auto_approve:
        config["auto_approve"] = True

    save_config(config)

    auto_approve = config.get("auto_approve", False)

    print(f"\n{BOLD}vLLM Chat{RESET} {DIM}(type /help for commands){RESET}")
    print(f"{DIM}Workspace: {CWD}{RESET}")
    if auto_approve:
        print(f"{YELLOW}Auto-approve: ON (actions execute without confirmation){RESET}")

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
            elif cmd == "/auto":
                auto_approve = not auto_approve
                config["auto_approve"] = auto_approve
                save_config(config)
                state = f"{GREEN}ON{RESET}" if auto_approve else f"{RED}OFF{RESET}"
                print(f"{DIM}Auto-approve: {state}\n")
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
                    messages = [m for m in messages if m["role"] != "system"]
                    messages.insert(0, {"role": "system", "content": build_system_prompt(config)})
                    print(f"{DIM}System prompt set.{RESET}\n")
                else:
                    print(f"System: {config.get('system', 'none')}\n")
            elif cmd == "/history":
                if len(messages) <= 1:
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

            # Process autonomous actions
            actions = process_actions(response, messages, auto_approve)
            if actions:
                # If there were read_file or run_command actions, send results back to model
                has_followup = any("Read " in a or "Ran " in a for a in actions)
                if has_followup:
                    print(f"{DIM}Sending action results back to model...{RESET}")
                    followup = stream_response(client, messages, config)
                    if followup:
                        messages.append({"role": "assistant", "content": followup})
                        last_response = followup
                        # Process any actions in the followup too
                        process_actions(followup, messages, auto_approve)
        else:
            messages.pop()


if __name__ == "__main__":
    main()
