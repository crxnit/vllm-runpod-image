#!/usr/bin/env python3
"""CLI chat interface for vLLM models on RunPod with workspace awareness."""

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

try:
    from openai import OpenAI, OpenAIError
except ImportError:
    print("Missing dependency. Install with: pip install openai")
    sys.exit(1)

from common import (
    CONFIG_PATH, BOLD, DIM, CYAN, GREEN, YELLOW, RED, MAGENTA, RESET,
    load_config, save_config,
)

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
IMPORTANT: You have tools to interact with the user's workspace. You MUST use them — never ask the user to paste file contents.

Tools (use these XML tags in your responses):

1. Read a file:
<read_file path="relative/path.py"/>

2. Write or create a file:
<write_file path="relative/path.py">
file contents here
</write_file>

3. Run a shell command:
<run_command>command here</run_command>

Rules:
- ALWAYS use <read_file> to read files yourself. NEVER ask the user to provide file contents.
- ALWAYS use <write_file> to create or edit files. Write complete file contents, not partial snippets.
- ALWAYS use <run_command> to run commands.
- The user will be asked to approve each action before it executes.
- You can include multiple actions in a single response.
- Use relative paths from the working directory.

Example — if the user says "review main.py", respond like this:
I'll read main.py and review it for you.
<read_file path="main.py"/>

Example — if the user says "create a hello world script", respond like this:
I'll create that for you.
<write_file path="hello.py">
print("Hello, world!")
</write_file>

Example — if the user says "what Python version is installed", respond like this:
Let me check.
<run_command>python3 --version</run_command>
"""


# =========================================================================
# Filesystem helpers
# =========================================================================

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


SAFE_PATH_PATTERN = re.compile(r'^[\w./\-]+$')


def safe_resolve_path(path_str):
    """Resolve path and verify it's within CWD to prevent traversal."""
    if not path_str or not SAFE_PATH_PATTERN.match(path_str):
        return None, f"Invalid path characters: {path_str!r}"
    if '\x00' in path_str:
        return None, f"Null byte in path: {path_str!r}"
    p = resolve_path(path_str)
    try:
        p.relative_to(CWD)
    except ValueError:
        return None, f"Path escapes workspace: {path_str} (resolves to {p})"
    return p, None


def read_file(path_str):
    path, err = safe_resolve_path(path_str)
    if err:
        return None, err
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
    except (OSError, PermissionError) as e:
        return None, f"Error reading {path}: {e}"


def write_file(path_str, content):
    path, err = safe_resolve_path(path_str)
    if err:
        return False, err
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        rel = path.relative_to(CWD) if path.is_relative_to(CWD) else path
        return True, str(rel)
    except (OSError, PermissionError) as e:
        return False, f"Error writing {path}: {e}"


# =========================================================================
# Shell execution
# =========================================================================

DANGEROUS_PATTERNS = re.compile(
    r"rm\s+-rf\s+/|mkfs|dd\s+if=|:(){ :|chmod\s+-R\s+777\s+/|>\s*/dev/sd",
    re.IGNORECASE,
)


def run_shell(cmd):
    if DANGEROUS_PATTERNS.search(cmd):
        return "(blocked: command matched a dangerous pattern)"
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
    except OSError as e:
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
        if answer == "":
            return True
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


# =========================================================================
# Workspace context (gathered once, passed to prompt builder)
# =========================================================================

def gather_workspace_context():
    """Collect workspace data (git branch, file listing) — side effects isolated here."""
    context = {"cwd": str(CWD)}

    git_dir = CWD / ".git"
    if git_dir.exists():
        context["git_branch"] = run_shell("git branch --show-current")

    try:
        entries = sorted(CWD.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        entries = [e for e in entries if not should_ignore(e.name)]
        listing = ", ".join(
            f"{e.name}/" if e.is_dir() else e.name for e in entries[:30]
        )
        if len(entries) > 30:
            listing += f", ... ({len(entries) - 30} more)"
        context["files"] = listing
    except (OSError, PermissionError) as e:
        context["files"] = f"(unable to list: {e})"

    return context


def build_system_prompt(config, workspace):
    """Pure function: assemble system prompt from config and pre-gathered workspace context."""
    parts = []

    user_system = config.get("system", "")
    if user_system:
        parts.append(user_system)

    parts.append(f"Working directory: {workspace['cwd']}")

    if "git_branch" in workspace:
        parts.append(f"Git branch: {workspace['git_branch']}")

    parts.append(f"Files: {workspace['files']}")

    parts.append(
        "You have access to the user's working directory. "
        "When the user asks about files, references code, or asks you to read/write files, "
        "work with the files in this directory. "
        "When writing code, be precise and match the existing style."
    )

    parts.append(AGENT_INSTRUCTIONS)

    return "\n\n".join(parts)


# =========================================================================
# Action handlers (each handles one type of autonomous action)
# =========================================================================

def validate_action_path(filepath):
    """Validate path for an action. Prints error and returns error string, or None if valid."""
    _, path_err = safe_resolve_path(filepath)
    if path_err:
        print(f"  {RED}Blocked: {path_err}{RESET}\n")
    return path_err


def handle_write_action(match, messages, auto_approve):
    """Handle a single <write_file> action."""
    filepath = match.group(1)
    content = match.group(2).strip()

    if validate_action_path(filepath):
        return f"Blocked write outside workspace: {filepath}"

    lines = content.count("\n") + 1
    print(f"{MAGENTA}Action: write {filepath} ({lines} lines){RESET}")

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
            return f"Wrote {info}"
        else:
            print(f"  {RED}{info}{RESET}\n")
            return f"Failed to write {filepath}: {info}"
    else:
        print(f"  {DIM}Skipped.{RESET}\n")
        return f"User skipped writing {filepath}"


def handle_read_action(match, messages, auto_approve):
    """Handle a single <read_file> action."""
    filepath = match.group(1)

    if validate_action_path(filepath):
        return f"Blocked read outside workspace: {filepath}"

    print(f"{MAGENTA}Action: read {filepath}{RESET}")
    content, info = read_file(filepath)
    if content:
        lines = content.count("\n") + 1
        print(f"  {DIM}Read {info} ({lines} lines){RESET}\n")
        file_msg = f"Contents of {info}:\n```\n{content}\n```"
        messages.append({"role": "user", "content": file_msg})
        return f"Read {info}"
    else:
        print(f"  {RED}{info}{RESET}\n")
        return f"Failed to read {filepath}: {info}"


def handle_run_action(match, messages, auto_approve):
    """Handle a single <run_command> action. Always requires confirmation."""
    cmd = match.group(1).strip()
    if not cmd:
        return None

    print(f"{MAGENTA}Action: run `{cmd}`{RESET}")

    if DANGEROUS_PATTERNS.search(cmd):
        print(f"  {RED}Blocked: command matched a dangerous pattern.{RESET}\n")
        return f"Blocked dangerous command: `{cmd}`"

    if confirm("Run this command?"):
        output = run_shell(cmd)
        print(f"  {DIM}{output}{RESET}\n")
        messages.append({"role": "user", "content": f"Command output for `{cmd}`:\n```\n{output}\n```"})
        return f"Ran `{cmd}`"
    else:
        print(f"  {DIM}Skipped.{RESET}\n")
        return f"User skipped running `{cmd}`"


# Action registry: (pattern, handler)
ACTION_HANDLERS = [
    (WRITE_FILE_PATTERN, handle_write_action),
    (READ_FILE_PATTERN, handle_read_action),
    (RUN_CMD_PATTERN, handle_run_action),
]


def process_actions(response, messages, auto_approve=False):
    """Parse and execute autonomous actions from model response."""
    actions_taken = []
    for pattern, handler in ACTION_HANDLERS:
        for match in pattern.finditer(response):
            result = handler(match, messages, auto_approve)
            if result:
                actions_taken.append(result)
    return actions_taken


# =========================================================================
# File context injection
# =========================================================================

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


# =========================================================================
# API client abstraction
# =========================================================================

class ChatClient:
    """Abstract chat completions client."""
    def stream(self, messages, model, max_tokens, temperature):
        raise NotImplementedError

    def list_models(self):
        raise NotImplementedError


class OpenAIChatClient(ChatClient):
    """Chat client backed by the OpenAI SDK."""
    def __init__(self, endpoint, api_key):
        self._client = OpenAI(
            base_url=endpoint.rstrip("/") + "/v1",
            api_key=api_key or "no-key",
        )

    def stream(self, messages, model, max_tokens, temperature):
        return self._client.chat.completions.create(
            model=model, messages=messages,
            max_tokens=max_tokens, temperature=temperature,
            stream=True,
        )

    def list_models(self):
        return self._client.models.list()


def get_client(config):
    endpoint = config.get("endpoint", "").rstrip("/")
    if not endpoint:
        print(f"{RED}No endpoint set. Use /endpoint URL{RESET}")
        return None
    return OpenAIChatClient(endpoint, config.get("key", "no-key"))


def stream_response(client, messages, config):
    try:
        stream = client.stream(
            messages,
            model=config.get("model", "/models/weights"),
            max_tokens=config.get("max_tokens", 1024),
            temperature=config.get("temperature", 0.7),
        )

        print(f"\n{GREEN}assistant{RESET}: ", end="", flush=True)
        full_response = ""

        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                print(delta.content, end="", flush=True)
                full_response += delta.content

        print("\n")
        return full_response

    except KeyboardInterrupt:
        print(f"\n{DIM}(cancelled){RESET}\n")
        return None
    except OpenAIError as e:
        print(f"\n{RED}API error: {e}{RESET}\n")
        return None
    except (ConnectionError, TimeoutError, OSError) as e:
        print(f"\n{RED}Connection error: {e}{RESET}\n")
        return None


# =========================================================================
# Chat context — mutable state for the REPL session
# =========================================================================

class ChatContext:
    """Holds mutable state for the chat session."""
    def __init__(self, config, messages):
        self.config = config
        self.messages = messages
        self.last_response = None
        self.auto_approve = config.get("auto_approve", False)

    def rebuild_system_prompt(self):
        workspace = gather_workspace_context()
        self.messages = [m for m in self.messages if m["role"] != "system"]
        self.messages.insert(0, {"role": "system", "content": build_system_prompt(self.config, workspace)})


# =========================================================================
# Command handlers (each returns 'quit' to exit, or None to continue)
# =========================================================================

def cmd_quit(arg, ctx):
    print(f"{DIM}Goodbye!{RESET}")
    return "quit"


def cmd_help(arg, ctx):
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


def cmd_clear(arg, ctx):
    ctx.rebuild_system_prompt()
    ctx.last_response = None
    print(f"{DIM}Conversation cleared.{RESET}\n")


def cmd_config(arg, ctx):
    c = ctx.config
    print(f"\n{BOLD}Configuration:{RESET}")
    print(f"  Endpoint:    {c.get('endpoint', DIM + 'not set' + RESET)}")
    print(f"  API Key:     {DIM}{'*' * 8 + c['key'][-4:] if c.get('key') else 'not set'}{RESET}")
    print(f"  Model:       {c.get('model', '/models/weights')}")
    print(f"  Temperature: {c.get('temperature', 0.7)}")
    print(f"  Max Tokens:  {c.get('max_tokens', 1024)}")
    print(f"  System:      {c.get('system', DIM + 'none' + RESET)}")
    print(f"  Auto-approve:{YELLOW} {'ON' if ctx.auto_approve else 'OFF'}{RESET}")
    print(f"  Config file: {DIM}{CONFIG_PATH}{RESET}")
    print(f"\n{BOLD}Workspace:{RESET}")
    print(f"  Directory:   {CWD}")
    git_dir = CWD / ".git"
    if git_dir.exists():
        branch = run_shell("git branch --show-current")
        print(f"  Git branch:  {branch}")
    print()


def cmd_auto(arg, ctx):
    ctx.auto_approve = not ctx.auto_approve
    ctx.config["auto_approve"] = ctx.auto_approve
    save_config(ctx.config)
    state = f"{GREEN}ON{RESET}" if ctx.auto_approve else f"{RED}OFF{RESET}"
    print(f"{DIM}Auto-approve: {state}\n")


def cmd_endpoint(arg, ctx):
    if arg:
        ctx.config["endpoint"] = arg.rstrip("/")
        save_config(ctx.config)
        print(f"{DIM}Endpoint set to {ctx.config['endpoint']}{RESET}\n")
    else:
        print(f"Endpoint: {ctx.config.get('endpoint', 'not set')}\n")


def cmd_key(arg, ctx):
    if arg:
        ctx.config["key"] = arg
        save_config(ctx.config)
        print(f"{DIM}API key updated.{RESET}\n")
    else:
        c = ctx.config
        print(f"API key: {'*' * 8 + c['key'][-4:] if c.get('key') else 'not set'}\n")


def cmd_model(arg, ctx):
    if arg:
        ctx.config["model"] = arg
        save_config(ctx.config)
        print(f"{DIM}Model set to {arg}{RESET}\n")
    else:
        print(f"Model: {ctx.config.get('model', '/models/weights')}\n")


def cmd_temp(arg, ctx):
    if arg:
        try:
            val = float(arg)
            if not 0 <= val <= 2:
                print(f"{RED}Temperature must be between 0 and 2.{RESET}\n")
            else:
                ctx.config["temperature"] = val
                save_config(ctx.config)
                print(f"{DIM}Temperature set to {val}{RESET}\n")
        except ValueError:
            print(f"{RED}Invalid temperature value.{RESET}\n")
    else:
        print(f"Temperature: {ctx.config.get('temperature', 0.7)}\n")


def cmd_max(arg, ctx):
    if arg:
        try:
            val = int(arg)
            if val < 1 or val > 16384:
                print(f"{RED}Max tokens must be between 1 and 16384.{RESET}\n")
            else:
                ctx.config["max_tokens"] = val
                save_config(ctx.config)
                print(f"{DIM}Max tokens set to {val}{RESET}\n")
        except ValueError:
            print(f"{RED}Invalid max tokens value.{RESET}\n")
    else:
        print(f"Max tokens: {ctx.config.get('max_tokens', 1024)}\n")


def cmd_system(arg, ctx):
    if arg:
        ctx.config["system"] = arg
        save_config(ctx.config)
        ctx.rebuild_system_prompt()
        print(f"{DIM}System prompt set.{RESET}\n")
    else:
        print(f"System: {ctx.config.get('system', 'none')}\n")


def cmd_history(arg, ctx):
    if len(ctx.messages) <= 1:
        print(f"{DIM}No messages.{RESET}\n")
    else:
        print()
        for m in ctx.messages:
            if m["role"] == "system":
                continue
            role_color = CYAN if m["role"] == "user" else GREEN
            content_preview = m["content"][:100] + ("..." if len(m["content"]) > 100 else "")
            print(f"  {role_color}{m['role']}{RESET}: {content_preview}")
        print()


def cmd_pwd(arg, ctx):
    print(f"  {CWD}\n")


def cmd_ls(arg, ctx):
    target = resolve_path(arg) if arg else CWD
    if not target.is_dir():
        print(f"{RED}Not a directory: {target}{RESET}\n")
        return
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
    except (OSError, PermissionError) as ex:
        print(f"{RED}Error: {ex}{RESET}\n")


def cmd_tree(arg, ctx):
    target = resolve_path(arg) if arg else CWD
    if not target.is_dir():
        print(f"{RED}Not a directory: {target}{RESET}\n")
        return
    print(f"\n  {BOLD}{target.name}/{RESET}")
    tree = get_tree(target)
    for line in tree.split("\n"):
        if line:
            print(f"  {line}")
    print()


def cmd_read(arg, ctx):
    if not arg:
        print(f"{RED}Usage: /read <file>{RESET}\n")
        return
    content, info = read_file(arg)
    if content is None:
        print(f"{RED}{info}{RESET}\n")
    else:
        lines = content.count("\n") + 1
        print(f"{DIM}Read {info} ({lines} lines) into conversation.{RESET}\n")
        file_msg = f"Contents of {info}:\n```\n{content}\n```"
        ctx.messages.append({"role": "user", "content": file_msg})


def cmd_write(arg, ctx):
    if not arg:
        print(f"{RED}Usage: /write <file>{RESET}\n")
    elif not ctx.last_response:
        print(f"{RED}No assistant response to write.{RESET}\n")
    else:
        code_blocks = re.findall(r"```(?:\w*\n)?(.*?)```", ctx.last_response, re.DOTALL)
        content = code_blocks[0].strip() if code_blocks else ctx.last_response
        ok, info = write_file(arg, content + "\n")
        if ok:
            print(f"{DIM}Wrote to {info}{RESET}\n")
        else:
            print(f"{RED}{info}{RESET}\n")


def cmd_diff(arg, ctx):
    if not arg:
        output = run_shell("git diff")
    else:
        path, err = safe_resolve_path(arg)
        if err:
            print(f"{RED}{err}{RESET}\n")
            return
        try:
            result = subprocess.run(
                ["git", "diff", "--", str(path)],
                capture_output=True, text=True, timeout=30, cwd=str(CWD)
            )
            output = (result.stdout + result.stderr).strip() or "(no changes)"
        except (subprocess.TimeoutExpired, OSError) as e:
            output = f"(error: {e})"
    print(f"\n{output}\n")


def cmd_sh(arg, ctx):
    if not arg:
        print(f"{RED}Usage: /sh <command>{RESET}\n")
    else:
        output = run_shell(arg)
        print(f"\n{output}\n")


# Command registry
COMMANDS = {
    "/quit": cmd_quit, "/exit": cmd_quit, "/q": cmd_quit,
    "/help": cmd_help,
    "/clear": cmd_clear,
    "/config": cmd_config,
    "/auto": cmd_auto,
    "/endpoint": cmd_endpoint,
    "/key": cmd_key,
    "/model": cmd_model,
    "/temp": cmd_temp,
    "/max": cmd_max,
    "/system": cmd_system,
    "/history": cmd_history,
    "/pwd": cmd_pwd,
    "/ls": cmd_ls,
    "/tree": cmd_tree,
    "/read": cmd_read,
    "/write": cmd_write,
    "/diff": cmd_diff,
    "/sh": cmd_sh,
}


# =========================================================================
# Input handling
# =========================================================================

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


# =========================================================================
# Main
# =========================================================================

def parse_args():
    parser = argparse.ArgumentParser(description="CLI chat for vLLM models")
    parser.add_argument("--endpoint", help="API endpoint URL")
    parser.add_argument("--key", help="API key")
    parser.add_argument("--model", help="Model name", default=None)
    parser.add_argument("--temperature", type=float, help="Temperature", default=None)
    parser.add_argument("--max-tokens", type=int, help="Max tokens", default=None)
    parser.add_argument("--system", help="System prompt", default=None)
    parser.add_argument("--auto-approve", action="store_true", help="Skip action confirmations")
    return parser.parse_args()


def apply_cli_overrides(config, args):
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
    return config


def print_welcome(config):
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
                models = client.list_models()
                model_id = models.data[0].id if models.data else "unknown"
                print(f"{DIM}Connected to {endpoint}{RESET}")
                print(f"{DIM}Model: {model_id}{RESET}")
            except (OpenAIError, ConnectionError, TimeoutError, OSError) as e:
                print(f"{YELLOW}Warning: could not connect to {endpoint}: {e}{RESET}")
    else:
        print(f"{YELLOW}No endpoint configured. Use /endpoint URL to set one.{RESET}")
    print()


def repl_loop(ctx):
    """Main REPL loop — reads input, dispatches commands or sends messages."""
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

            handler = COMMANDS.get(cmd)
            if handler:
                result = handler(arg, ctx)
                if result == "quit":
                    break
            else:
                print(f"{RED}Unknown command: {cmd}. Type /help for commands.{RESET}\n")
            continue

        # Process @file references
        text, files_added = inject_file_context(text)
        if files_added:
            filenames = ", ".join(f[0] for f in files_added)
            print(f"{DIM}Attached: {filenames}{RESET}")

        # Send message
        client = get_client(ctx.config)
        if not client:
            continue

        ctx.messages.append({"role": "user", "content": text})
        response = stream_response(client, ctx.messages, ctx.config)

        if response:
            ctx.messages.append({"role": "assistant", "content": response})
            ctx.last_response = response

            # Process autonomous actions
            actions = process_actions(response, ctx.messages, ctx.auto_approve)
            if actions:
                has_followup = any("Read " in a or "Ran " in a for a in actions)
                if has_followup:
                    print(f"{DIM}Sending action results back to model...{RESET}")
                    followup = stream_response(client, ctx.messages, ctx.config)
                    if followup:
                        ctx.messages.append({"role": "assistant", "content": followup})
                        ctx.last_response = followup
                        process_actions(followup, ctx.messages, ctx.auto_approve)
        else:
            ctx.messages.pop()


def main():
    args = parse_args()
    config = load_config()
    config = apply_cli_overrides(config, args)
    save_config(config)

    print_welcome(config)

    workspace = gather_workspace_context()
    system_prompt = build_system_prompt(config, workspace)
    messages = [{"role": "system", "content": system_prompt}]
    ctx = ChatContext(config, messages)

    repl_loop(ctx)


if __name__ == "__main__":
    main()
