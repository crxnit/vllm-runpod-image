#!/usr/bin/env python3
"""Load test for vLLM endpoints on RunPod."""

import argparse
import asyncio
import json
import time
import sys

try:
    import aiohttp
except ImportError:
    print("Missing dependency. Install with: pip install aiohttp")
    sys.exit(1)

from common import BOLD, DIM, CYAN, GREEN, YELLOW, RED, RESET, load_config

TEST_PROMPTS = [
    "Write a Python function that checks if a string is a palindrome.",
    "Explain the difference between a stack and a queue in 3 sentences.",
    "Write a bash one-liner that finds all files larger than 100MB.",
    "What is the time complexity of binary search? Explain briefly.",
    "Write a Python class for a linked list with insert and delete methods.",
    "Explain what a mutex is and when you would use one.",
    "Write a SQL query that finds duplicate email addresses in a users table.",
    "What are the SOLID principles? List them briefly.",
    "Write a Python decorator that retries a function up to 3 times on exception.",
    "Explain the CAP theorem in simple terms.",
    "Write a function to merge two sorted arrays in O(n) time.",
    "What is the difference between TCP and UDP?",
    "Write a Python generator that yields Fibonacci numbers.",
    "Explain how a hash table handles collisions.",
    "Write a regular expression that validates an email address.",
    "What is the difference between concurrency and parallelism?",
    "Write a Python function that flattens a nested list.",
    "Explain what a database index is and when to use one.",
    "Write a binary search implementation in Python.",
    "What is dependency injection and why is it useful?",
]


async def send_request(session, url, headers, payload, request_id):
    """Send a single chat completion request and measure timing."""
    start = time.monotonic()
    first_token_time = None
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    error = None

    try:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status != 200:
                body = await resp.text()
                return {
                    "id": request_id,
                    "error": f"HTTP {resp.status}: {body[:200]}",
                    "duration": time.monotonic() - start,
                }

            # Stream the response
            buffer = ""
            async for chunk in resp.content:
                if first_token_time is None:
                    first_token_time = time.monotonic()

                buffer += chunk.decode("utf-8", errors="ignore")
                lines = buffer.split("\n")
                buffer = lines[-1]

                for line in lines[:-1]:
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                        delta = obj.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            completion_tokens += len(delta.split())
                        usage = obj.get("usage")
                        if usage:
                            prompt_tokens = usage.get("prompt_tokens", 0)
                            completion_tokens = usage.get("completion_tokens", completion_tokens)
                            total_tokens = usage.get("total_tokens", 0)
                    except json.JSONDecodeError:
                        pass

    except asyncio.CancelledError:
        error = "cancelled"
    except aiohttp.ClientError as e:
        error = f"connection error: {e}"
    except asyncio.TimeoutError:
        error = "request timed out"
    except OSError as e:
        error = str(e)

    end = time.monotonic()
    duration = end - start
    ttft = (first_token_time - start) if first_token_time else None

    return {
        "id": request_id,
        "duration": duration,
        "ttft": ttft,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "error": error,
    }


async def run_batch(endpoint, key, model, max_tokens, temperature, concurrency, num_requests):
    """Run a batch of concurrent requests."""
    url = endpoint.rstrip("/") + "/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }

    results = []
    semaphore = asyncio.Semaphore(concurrency)

    async def limited_request(i):
        async with semaphore:
            prompt = TEST_PROMPTS[i % len(TEST_PROMPTS)]
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,
            }
            return await send_request(session, url, headers, payload, i)

    connector = aiohttp.TCPConnector(limit=concurrency + 10)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [limited_request(i) for i in range(num_requests)]
        results = await asyncio.gather(*tasks)

    return results


def print_results(results, concurrency):
    """Print summary statistics for a batch."""
    successful = [r for r in results if not r.get("error")]
    failed = [r for r in results if r.get("error")]

    if not successful:
        print(f"  {RED}All {len(results)} requests failed.{RESET}")
        for r in failed[:3]:
            print(f"    {DIM}{r['error'][:100]}{RESET}")
        return None

    durations = [r["duration"] for r in successful]
    ttfts = [r["ttft"] for r in successful if r["ttft"] is not None]
    total_completion_tokens = sum(r["completion_tokens"] for r in successful)
    total_duration = max(durations)

    avg_dur = sum(durations) / len(durations)
    p50_dur = sorted(durations)[len(durations) // 2]
    p99_dur = sorted(durations)[int(len(durations) * 0.99)]
    avg_ttft = sum(ttfts) / len(ttfts) if ttfts else 0
    throughput = total_completion_tokens / total_duration if total_duration > 0 else 0

    stats = {
        "concurrency": concurrency,
        "requests": len(results),
        "successful": len(successful),
        "failed": len(failed),
        "avg_duration": avg_dur,
        "p50_duration": p50_dur,
        "p99_duration": p99_dur,
        "avg_ttft": avg_ttft,
        "throughput": throughput,
        "total_tokens": total_completion_tokens,
        "wall_time": total_duration,
    }

    return stats


def print_summary_table(all_stats):
    """Print a summary table across all concurrency levels."""
    print(f"\n{BOLD}{'Conc':>5} {'Reqs':>5} {'OK':>4} {'Fail':>4} {'Avg(s)':>7} {'P50(s)':>7} {'P99(s)':>7} {'TTFT(s)':>8} {'Tok/s':>7}{RESET}")
    print(f"{DIM}{'─' * 62}{RESET}")

    for s in all_stats:
        fail_color = RED if s["failed"] > 0 else ""
        fail_reset = RESET if s["failed"] > 0 else ""
        print(
            f"{s['concurrency']:>5} "
            f"{s['requests']:>5} "
            f"{GREEN}{s['successful']:>4}{RESET} "
            f"{fail_color}{s['failed']:>4}{fail_reset} "
            f"{s['avg_duration']:>7.2f} "
            f"{s['p50_duration']:>7.2f} "
            f"{s['p99_duration']:>7.2f} "
            f"{s['avg_ttft']:>8.3f} "
            f"{CYAN}{s['throughput']:>7.1f}{RESET}"
        )


async def main_async(args):
    config = load_config()
    endpoint = args.endpoint or config.get("endpoint", "")
    key = args.key or config.get("key", "no-key")
    model = args.model or config.get("model", "/models/weights")

    if not endpoint:
        print(f"{RED}No endpoint set. Use --endpoint URL{RESET}")
        return

    try:
        concurrency_levels = [int(x) for x in args.concurrency.split(",")]
        if any(c < 1 for c in concurrency_levels):
            raise ValueError("concurrency must be positive")
    except ValueError as e:
        print(f"{RED}Invalid concurrency levels: {e}{RESET}")
        return
    num_requests = max(1, args.requests)

    print(f"\n{BOLD}vLLM Load Test{RESET}")
    print(f"{DIM}Endpoint:    {endpoint}{RESET}")
    print(f"{DIM}Model:       {model}{RESET}")
    print(f"{DIM}Max tokens:  {args.max_tokens}{RESET}")
    print(f"{DIM}Temperature: {args.temperature}{RESET}")
    print(f"{DIM}Requests:    {num_requests} per concurrency level{RESET}")
    print(f"{DIM}Concurrency: {concurrency_levels}{RESET}")

    # Quick connectivity check
    print(f"\n{DIM}Checking connectivity...{RESET}", end=" ", flush=True)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                endpoint.rstrip("/") + "/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    model_name = data.get("data", [{}])[0].get("id", "unknown")
                    print(f"{GREEN}OK{RESET} ({model_name})")
                else:
                    print(f"{RED}HTTP {resp.status}{RESET}")
                    return
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        print(f"{RED}Failed: {e}{RESET}")
        return

    all_stats = []

    for conc in concurrency_levels:
        print(f"\n{YELLOW}Running {num_requests} requests at concurrency {conc}...{RESET}", flush=True)
        start = time.monotonic()
        results = await run_batch(endpoint, key, model, args.max_tokens, args.temperature, conc, num_requests)
        wall = time.monotonic() - start

        stats = print_results(results, conc)
        if stats:
            all_stats.append(stats)
            print(
                f"  {DIM}Wall time: {wall:.1f}s | "
                f"Tokens generated: {stats['total_tokens']} | "
                f"Throughput: {CYAN}{stats['throughput']:.1f} tok/s{RESET}"
            )

            # Print any errors
            failed = [r for r in results if r.get("error")]
            if failed:
                for r in failed[:3]:
                    print(f"  {RED}Request {r['id']}: {r['error'][:100]}{RESET}")
        else:
            print(f"  {RED}Batch failed.{RESET}")

    if all_stats:
        print_summary_table(all_stats)

    print()


def main():
    parser = argparse.ArgumentParser(description="Load test for vLLM endpoints")
    parser.add_argument("--endpoint", help="API endpoint URL")
    parser.add_argument("--key", help="API key")
    parser.add_argument("--model", help="Model name")
    parser.add_argument("--max-tokens", type=int, default=256, help="Max tokens per response (default: 256)")
    parser.add_argument("--temperature", type=float, default=0.3, help="Temperature (default: 0.3)")
    parser.add_argument("--requests", type=int, default=10, help="Requests per concurrency level (default: 10)")
    parser.add_argument(
        "--concurrency",
        default="1,5,10,20",
        help="Comma-separated concurrency levels (default: 1,5,10,20)",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
