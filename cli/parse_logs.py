#!/usr/bin/env python3
"""Parse vLLM container logs and extract metrics to CSV."""

import argparse
import csv
import re
import sys
from pathlib import Path

# Match vLLM engine stats lines like:
# Engine 000: Avg prompt throughput: 3.5 tokens/s, Avg generation throughput: 8.5 tokens/s,
# Running: 0 reqs, Waiting: 0 reqs, GPU KV cache usage: 0.0%, Prefix cache hit rate: 0.0%
ENGINE_PATTERN = re.compile(
    r"(?P<timestamp>\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}).*?"
    r"Avg prompt throughput:\s*(?P<prompt_tps>[\d.]+)\s*tokens/s.*?"
    r"Avg generation throughput:\s*(?P<gen_tps>[\d.]+)\s*tokens/s.*?"
    r"Running:\s*(?P<running>\d+)\s*reqs.*?"
    r"Waiting:\s*(?P<waiting>\d+)\s*reqs.*?"
    r"GPU KV cache usage:\s*(?P<kv_cache>[\d.]+)%.*?"
    r"Prefix cache hit rate:\s*(?P<prefix_cache>[\d.]+)%"
)

# Match HTTP request lines like:
# INFO:     100.64.1.67:46476 - "POST /v1/chat/completions HTTP/1.1" 200 OK
REQUEST_PATTERN = re.compile(
    r"(?P<timestamp>\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}).*?"
    r'"(?P<method>\w+)\s+(?P<path>/\S+)\s+HTTP/[\d.]+"'
    r"\s+(?P<status>\d+)"
)


def parse_logs(input_file):
    """Parse vLLM log lines and return engine stats and request records."""
    engine_stats = []
    requests = []

    for line in input_file:
        line = line.strip()
        if not line:
            continue

        # Try engine stats
        m = ENGINE_PATTERN.search(line)
        if m:
            engine_stats.append({
                "timestamp": m.group("timestamp"),
                "prompt_throughput_tps": float(m.group("prompt_tps")),
                "generation_throughput_tps": float(m.group("gen_tps")),
                "running_reqs": int(m.group("running")),
                "waiting_reqs": int(m.group("waiting")),
                "kv_cache_pct": float(m.group("kv_cache")),
                "prefix_cache_hit_pct": float(m.group("prefix_cache")),
            })
            continue

        # Try request lines
        m = REQUEST_PATTERN.search(line)
        if m:
            requests.append({
                "timestamp": m.group("timestamp"),
                "method": m.group("method"),
                "path": m.group("path"),
                "status": int(m.group("status")),
            })

    return engine_stats, requests


def write_csv(records, output_path, fieldnames):
    """Write records to a CSV file."""
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def print_summary(engine_stats, requests):
    """Print a summary of parsed data."""
    if not engine_stats:
        print("  No engine stats found.")
        return

    gen_tps = [s["generation_throughput_tps"] for s in engine_stats]
    prompt_tps = [s["prompt_throughput_tps"] for s in engine_stats]
    kv = [s["kv_cache_pct"] for s in engine_stats]
    running = [s["running_reqs"] for s in engine_stats]
    waiting = [s["waiting_reqs"] for s in engine_stats]

    # Filter out idle entries for throughput stats
    active_gen = [t for t in gen_tps if t > 0]
    active_prompt = [t for t in prompt_tps if t > 0]

    print(f"\n\033[1mLog Summary\033[0m")
    print(f"  Engine stat entries:  {len(engine_stats)}")
    print(f"  HTTP requests:        {len(requests)}")
    print()
    print(f"\033[1mGeneration Throughput (tokens/s)\033[0m")
    if active_gen:
        print(f"  Avg:  {sum(active_gen) / len(active_gen):.1f}")
        print(f"  Max:  {max(active_gen):.1f}")
        print(f"  Min:  {min(active_gen):.1f}")
    else:
        print(f"  No active generation periods found.")
    print()
    print(f"\033[1mPrompt Throughput (tokens/s)\033[0m")
    if active_prompt:
        print(f"  Avg:  {sum(active_prompt) / len(active_prompt):.1f}")
        print(f"  Max:  {max(active_prompt):.1f}")
        print(f"  Min:  {min(active_prompt):.1f}")
    else:
        print(f"  No active prompt periods found.")
    print()
    print(f"\033[1mGPU KV Cache Usage (%)\033[0m")
    print(f"  Avg:  {sum(kv) / len(kv):.1f}")
    print(f"  Max:  {max(kv):.1f}")
    print()
    print(f"\033[1mConcurrency\033[0m")
    print(f"  Max running:  {max(running)}")
    print(f"  Max waiting:  {max(waiting)}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Parse vLLM logs to CSV")
    parser.add_argument(
        "input",
        nargs="?",
        default="-",
        help="Log file path (default: stdin)",
    )
    parser.add_argument(
        "-o", "--output",
        default="vllm_metrics.csv",
        help="Output CSV file (default: vllm_metrics.csv)",
    )
    parser.add_argument(
        "--requests-csv",
        default=None,
        help="Also write HTTP requests to this CSV file",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress summary output",
    )
    args = parser.parse_args()

    # Read input
    if args.input == "-":
        if sys.stdin.isatty():
            print("Paste log output below (Ctrl+D when done):\n")
        input_file = sys.stdin
    else:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"File not found: {args.input}")
            sys.exit(1)
        input_file = open(input_path)

    engine_stats, requests = parse_logs(input_file)

    if input_file is not sys.stdin:
        input_file.close()

    if not engine_stats and not requests:
        print("No vLLM metrics found in input.")
        sys.exit(1)

    # Write engine stats CSV
    if engine_stats:
        engine_fields = [
            "timestamp",
            "prompt_throughput_tps",
            "generation_throughput_tps",
            "running_reqs",
            "waiting_reqs",
            "kv_cache_pct",
            "prefix_cache_hit_pct",
        ]
        write_csv(engine_stats, args.output, engine_fields)
        print(f"Wrote {len(engine_stats)} engine stats to {args.output}")

    # Write requests CSV
    if requests and args.requests_csv:
        request_fields = ["timestamp", "method", "path", "status"]
        write_csv(requests, args.requests_csv, request_fields)
        print(f"Wrote {len(requests)} requests to {args.requests_csv}")

    if not args.quiet:
        print_summary(engine_stats, requests)


if __name__ == "__main__":
    main()
