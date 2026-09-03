#!/usr/bin/env python3
"""Measure streaming TTFT and decode TPS at short and long input contexts.

The generated request identifiers are placed at the *start* of each prompt.  This
prevents an in-process prompt/prefix cache from turning a later measurement into
a cache hit.  The server-reported prompt token count is retained in every run;
the requested input size is deliberately only a target because tokenizers differ.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


def percentile(values: list[float], value: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * value
    low, high = int(index), min(int(index) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def stats(values: list[float]) -> dict[str, float]:
    return {
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "p50": statistics.median(values),
        "p95": percentile(values, 0.95),
    }


def process_cpu_ticks(pid: int) -> int:
    """Return aggregate user+system ticks for one Linux process."""
    stat = Path(f"/proc/{pid}/stat").read_text()
    # comm is parenthesized and may contain spaces, so split only after its
    # final right parenthesis. Index 11/12 are utime/stime in the remainder.
    fields = stat[stat.rfind(")") + 2 :].split()
    return int(fields[11]) + int(fields[12])


class ProcessCpuSampler:
    """Sample one server process without including client-side benchmark CPU."""

    def __init__(self, pid: int, interval_ms: int) -> None:
        self.pid = pid
        self.interval_s = interval_ms / 1000
        self.hz = os.sysconf("SC_CLK_TCK")
        self.samples: list[tuple[int, int]] = []
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def _sample(self) -> None:
        try:
            self.samples.append((time.perf_counter_ns(), process_cpu_ticks(self.pid)))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass

    def start(self) -> None:
        self._sample()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval_s):
            self._sample()

    def stop(self) -> dict | None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=self.interval_s + 1)
        self._sample()
        if len(self.samples) < 2:
            return None
        start_ns, start_ticks = self.samples[0]
        end_ns, end_ticks = self.samples[-1]
        elapsed_s = (end_ns - start_ns) / 1_000_000_000
        cpu_s = (end_ticks - start_ticks) / self.hz
        interval_pcts = []
        for (left_ns, left_ticks), (right_ns, right_ticks) in zip(self.samples, self.samples[1:]):
            interval_s = (right_ns - left_ns) / 1_000_000_000
            if interval_s > 0:
                interval_pcts.append(((right_ticks - left_ticks) / self.hz) / interval_s * 100)
        return {
            "pid": self.pid,
            "sample_interval_ms": round(self.interval_s * 1000),
            "samples": len(self.samples),
            "elapsed_s": elapsed_s,
            "cpu_seconds": cpu_s,
            "avg_cpu_pct": (cpu_s / elapsed_s * 100) if elapsed_s > 0 else 0,
            "peak_interval_cpu_pct": max(interval_pcts, default=0),
            "cpu_core_equivalents": (cpu_s / elapsed_s) if elapsed_s > 0 else 0,
        }


def prompt(target_words: int, request_id: str) -> str:
    # A leading unique identifier prevents prefix-cache reuse.  " telemetry" is
    # intentionally simple, stable filler that most BPE tokenizers encode in a
    # predictable number of tokens; usage.prompt_tokens is the authoritative
    # measurement, not target_words.
    filler = " telemetry" * target_words
    return (
        f"Benchmark request identifier {request_id}."
        f"{filler}\n\n"
        "Read the material above silently. Reply with exactly one short sentence "
        "confirming that the benchmark payload was received."
    )


def post_stream(
    url: str, payload: dict, timeout: int, cpu_pid: int | None, cpu_sample_ms: int
) -> tuple[dict, list[dict]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter_ns()
    first_content_ns: int | None = None
    stream_end_ns: int | None = None
    usage: dict | None = None
    timings: dict | None = None
    chunks: list[dict] = []
    sampler = ProcessCpuSampler(cpu_pid, cpu_sample_ms) if cpu_pid is not None else None
    if sampler:
        sampler.start()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                now = time.perf_counter_ns()
                if data == "[DONE]":
                    stream_end_ns = now
                    break
                event = json.loads(data)
                chunks.append({"at_ms": (now - started) / 1_000_000, "event": event})
                if event.get("usage"):
                    usage = event["usage"]
                if event.get("timings"):
                    timings = event["timings"]
                for choice in event.get("choices", []):
                    if (choice.get("delta") or {}).get("content") and first_content_ns is None:
                        first_content_ns = now
    finally:
        cpu_telemetry = sampler.stop() if sampler else None
    if first_content_ns is None or stream_end_ns is None or usage is None or timings is None:
        raise RuntimeError("incomplete stream: content, [DONE], usage, or llama.cpp timings was absent")
    try:
        completion_tokens = int(usage["completion_tokens"])
        decode_ms = float(timings["predicted_ms"])
        predicted_tokens = int(timings["predicted_n"])
        server_tps = float(timings["predicted_per_second"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid llama.cpp timing payload: {timings!r}") from exc
    if predicted_tokens != completion_tokens:
        raise RuntimeError(
            "llama.cpp timing token count does not match streamed completion usage: "
            f"predicted_n={predicted_tokens!r}, completion_tokens={completion_tokens!r}"
        )
    if completion_tokens <= 0 or decode_ms <= 0:
        raise RuntimeError("invalid completion token count or decode duration")
    result = {
        "input_tokens": usage["prompt_tokens"],
        "output_tokens": completion_tokens,
        "ttft_ms": (first_content_ns - started) / 1_000_000,
        "decode_ms": decode_ms,
        "duration_ms": (stream_end_ns - started) / 1_000_000,
        "tps": server_tps,
        "client_post_first_content_ms": (stream_end_ns - first_content_ns) / 1_000_000,
    }
    if cpu_telemetry is not None:
        result["server_cpu"] = cpu_telemetry
    return result, chunks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8090/v1")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--backend", default="sycl-level-zero")
    parser.add_argument("--backend-args", required=True)
    parser.add_argument("--ctx-size", type=int, default=32768)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--warmup-runs", type=int, default=1)
    parser.add_argument("--short-input-words", type=int, default=32)
    parser.add_argument("--long-input-words", type=int, default=16384)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--cpu-pid", type=int, help="Host PID of the server process to sample via /proc.")
    parser.add_argument("--cpu-sample-ms", type=int, default=200)
    parser.add_argument("--output-filename", default="bench-sycl-qwen36-35b-context.json")
    args = parser.parse_args()
    if args.runs < 1 or args.warmup_runs < 0:
        parser.error("runs must be >= 1 and warmup-runs must be >= 0")
    if args.long_input_words + args.max_tokens >= args.ctx_size:
        parser.error("long input target plus output budget must be smaller than ctx-size")

    scenarios = (("context-short", args.short_input_words), ("context-long", args.long_input_words))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    request_url = f"{args.base_url.rstrip('/')}/chat/completions"
    measurements: dict[str, list[dict]] = {name: [] for name, _ in scenarios}
    failures: dict[str, dict[str, list[dict]]] = {
        name: {"warmup": [], "measurement": []} for name, _ in scenarios
    }

    for phase, count in (("warmup", args.warmup_runs), ("measurement", args.runs)):
        for run_number in range(1, count + 1):
            for name, target_words in scenarios:
                request_id = f"{phase}-{name}-{run_number}-{time.time_ns()}"
                payload = {
                    "model": args.model,
                    "messages": [{"role": "user", "content": prompt(target_words, request_id)}],
                    "temperature": 0,
                    "top_p": 1,
                    "max_tokens": args.max_tokens,
                    # Keep every decode sample at the requested length.  A
                    # natural early EOS (7 tokens for this prompt/model) makes
                    # client-side TPS too sensitive to stream-finalization.
                    "ignore_eos": True,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                }
                print(f"starting {phase} {name} run {run_number}/{count}", flush=True)
                try:
                    result, chunks = post_stream(
                        request_url, payload, args.timeout, args.cpu_pid, args.cpu_sample_ms
                    )
                    (args.output_dir / f"{phase}-{name}-{run_number:02d}.jsonl").write_text(
                        "\n".join(json.dumps(chunk) for chunk in chunks) + "\n"
                    )
                    result.update({"run_number": run_number, "target_input_words": target_words})
                    print(
                        f"finished {phase} {name} run {run_number}/{count}: "
                        f"input_tokens={result['input_tokens']} ttft_ms={result['ttft_ms']:.3f} "
                        f"tps={result['tps']:.3f}"
                        + (f" cpu_avg_pct={result['server_cpu']['avg_cpu_pct']:.2f}" if result.get("server_cpu") else ""),
                        flush=True,
                    )
                    if phase == "measurement":
                        measurements[name].append(result)
                except (RuntimeError, urllib.error.URLError, TimeoutError) as exc:
                    failure = {"run_number": run_number, "phase": phase, "error": str(exc)}
                    failures[name][phase].append(failure)
                    print(f"FAILED {phase} {name} run {run_number}/{count}: {exc}", flush=True)

    output_scenarios = []
    for name, target_words in scenarios:
        successful = measurements[name]
        if not successful:
            raise RuntimeError(
                f"all measured {name} runs failed: {failures[name]['measurement']}"
            )
        scenario = {
            "name": name,
            "target_input_words": target_words,
            "failed_runs": len(failures[name]["measurement"]),
            "warmup_failed_runs": len(failures[name]["warmup"]),
            "input_tokens": stats([item["input_tokens"] for item in successful]),
            "output_tokens": stats([item["output_tokens"] for item in successful]),
            "ttft_ms": stats([item["ttft_ms"] for item in successful]),
            "tps": stats([item["tps"] for item in successful]),
            "duration_ms": stats([item["duration_ms"] for item in successful]),
            "client_post_first_content_ms": stats(
                [item["client_post_first_content_ms"] for item in successful]
            ),
            "runs": successful,
            "failures": failures[name],
        }
        cpu_runs = [item["server_cpu"] for item in successful if item.get("server_cpu")]
        if cpu_runs:
            scenario["server_cpu"] = {
                "avg_cpu_pct": stats([item["avg_cpu_pct"] for item in cpu_runs]),
                "peak_interval_cpu_pct": stats([item["peak_interval_cpu_pct"] for item in cpu_runs]),
                "cpu_seconds": stats([item["cpu_seconds"] for item in cpu_runs]),
                "cpu_core_equivalents": stats([item["cpu_core_equivalents"] for item in cpu_runs]),
            }
        output_scenarios.append(scenario)
    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "measurement_method": {
            "ttft": "client monotonic time from POST start to first streamed content token",
            "tps": "llama.cpp server-reported predicted_per_second over its complete predicted-token interval",
            "cache_control": "unique request identifier at prompt start prevents prefix-cache reuse",
            "decode_control": "ignore_eos=true requests a fixed maximum-token decode window",
            "cpu_telemetry": "optional server host-PID /proc user+system CPU sampling; excludes benchmark client CPU",
        },
        "models": [{"model": args.model, "config": {"measurement_runs": args.runs, "warmup_runs": args.warmup_runs}, "results": [{
            "backend": args.backend, "backend_args": args.backend_args, "ctx_size": args.ctx_size,
            "recipe": "llamacpp-native-sycl-level-zero", "scenarios": output_scenarios,
        }]}],
    }
    (args.output_dir / args.output_filename).write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
