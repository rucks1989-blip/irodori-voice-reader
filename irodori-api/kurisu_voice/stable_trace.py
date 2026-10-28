"""Parse the safe rescue/timing markers emitted by the stable audio.cpp build.

The stable backend is a resident executable and writes its diagnostic trace
itself.  This module deliberately reads only numeric rescue/timing markers;
request text, paths and audio bytes are never returned to the API caller.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path


_MARKER = re.compile(
    r"^\[(?P<kind>TRACE|TIMING) ts=(?P<trace_ts>\d{8}-\d{6})\]\s+"
    r"(?P<key>\S+?)(?:\s+(?P<value>.*))?$"
)
_NUMERIC = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def stable_trace_path(root: Path) -> Path:
    """Return the stable trace path without creating or changing anything."""
    root = Path(root).resolve()
    preferred = root / "logs" / "audio_cpp_voice_stable_speaker" / "trace.log"
    if preferred.exists():
        return preferred
    return preferred


def trace_offset(path: Path) -> int:
    try:
        return int(Path(path).stat().st_size)
    except OSError:
        return 0


def _value(raw: str | None):
    value = str(raw or "").strip()
    if not value:
        return None
    if value in {"nan", "NaN"}:
        return "nan"
    if value in {"inf", "+inf", "Infinity", "+Infinity"}:
        return "inf"
    if value in {"-inf", "-Infinity"}:
        return "-inf"
    if _NUMERIC.match(value):
        try:
            number = float(value)
            return int(number) if number.is_integer() else number
        except ValueError:
            pass
    return value[:160]


def _trace_epoch(trace_ts: str) -> float:
    try:
        value = datetime.strptime(trace_ts, "%Y%m%d-%H%M%S")
        return value.replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


def _finish_attempt(attempts: list[dict], current: dict | None, end_ts: str | None) -> None:
    if not current:
        return
    record = dict(current)
    record["attempt"] = len(attempts)
    if end_ts:
        record["trace_end"] = end_ts
    start = _trace_epoch(str(record.get("trace_start") or ""))
    end = _trace_epoch(str(record.get("trace_end") or ""))
    if start and end and end >= start:
        # The native trace timestamp has one-second precision.  Keep that
        # explicit so callers do not mistake it for a high-resolution timer.
        record["elapsed_ms"] = round((end - start) * 1000.0, 1)
        record["elapsed_precision"] = "1s"
    record.setdefault("result", "complete")
    attempts.append(record)


def parse_trace_lines(lines: list[str]) -> dict:
    """Summarize rescue attempts from trace lines for unit tests and the API."""
    attempts: list[dict] = []
    current: dict | None = None
    last_ts = None
    session_wall_ms = None
    saw_stable_marker = False

    for line in lines:
        match = _MARKER.match(str(line).rstrip("\r\n"))
        if not match:
            continue
        kind = match.group("kind")
        trace_ts = match.group("trace_ts")
        key = match.group("key")
        value = _value(match.group("value"))
        last_ts = trace_ts
        if ".speaker_rescue_" in key or ".audio_quality_rescue_" in key:
            saw_stable_marker = True

        is_attempt_marker = key.endswith(".speaker_rescue_attempt") or key.endswith(".audio_quality_rescue_attempt")
        if is_attempt_marker:
            field = "speaker_rescue_attempt" if key.endswith(".speaker_rescue_attempt") else "audio_quality_rescue_attempt"
            if current is None:
                current = {"trace_start": trace_ts}
            known = current.get(field)
            if known is not None and known != value:
                # Both rescue counters are emitted around one generation.
                # Carry the other counter into the next record so a change in
                # speaker rescue after an audio-quality retry is not merged
                # into the previous attempt.
                previous = {
                    name: current.get(name)
                    for name in ("speaker_rescue_attempt", "audio_quality_rescue_attempt")
                    if current.get(name) is not None
                }
                _finish_attempt(attempts, current, trace_ts)
                current = {"trace_start": trace_ts, **previous}
            current[field] = value
            continue

        if current is None:
            if key.endswith(".effective_seed"):
                current = {"trace_start": trace_ts}
            else:
                continue

        if key.endswith(".effective_seed"):
            current["seed"] = value
        elif key.endswith(".speaker_rescue_probe_rms"):
            current["speaker_probe_rms"] = value
        elif key.endswith(".speaker_rescue_probe_threshold"):
            current["speaker_probe_threshold"] = value
        elif key.endswith(".speaker_rescue_triggered") and value:
            current["retry_reason"] = "speaker_rescue"
            current["result"] = "retry"
        elif key.endswith(".audio_quality_rescue_max_sample_jump"):
            current["audio_quality_max_sample_jump"] = value
        elif key.endswith(".audio_quality_rescue_non_finite_samples"):
            current["audio_quality_non_finite_samples"] = value
        elif key.endswith(".audio_quality_rescue_triggered") and value:
            current["retry_reason"] = "audio_quality_rescue"
            current["result"] = "retry"
        elif key.endswith(".speaker_rescue_final_attempt"):
            current["speaker_final_attempt"] = value
            current["result"] = "final"
        elif key.endswith(".speaker_rescue_final_seed"):
            current["speaker_final_seed"] = value
        elif key.endswith(".audio_quality_rescue_final_attempt"):
            current["audio_quality_final_attempt"] = value
            current["result"] = "final"
        elif key.endswith(".audio_quality_rescue_final_seed"):
            current["audio_quality_final_seed"] = value
        elif kind == "TIMING" and key.endswith("session.wall_ms"):
            session_wall_ms = value

    _finish_attempt(attempts, current, last_ts)
    # Avoid exposing arbitrary backend keys.  Keep only the safe fields needed
    # to diagnose retries and lock-vs-synthesis latency.
    for item in attempts:
        item.setdefault("speaker_rescue_attempt", 0)
        item.setdefault("audio_quality_rescue_attempt", 0)
        if "seed" not in item:
            item["seed"] = item.get("speaker_final_seed") or item.get("audio_quality_final_seed")
        item["result"] = "retry" if item.get("retry_reason") else item.get("result", "complete")
    return {
        "attempts": attempts[-8:],
        "session_wall_ms": session_wall_ms,
        "trace_end": last_ts,
        "stable_markers": saw_stable_marker,
    }


def collect_trace(path: Path, offset: int) -> dict:
    """Read only newly appended trace lines and return a safe summary."""
    path = Path(path)
    try:
        size = path.stat().st_size
        start = int(offset) if 0 <= int(offset) <= size else 0
        with path.open("rb") as stream:
            stream.seek(start)
            raw = stream.read()
    except OSError:
        return {"attempts": [], "session_wall_ms": None, "trace_end": None, "stable_markers": False}
    text = raw.decode("utf-8", errors="replace")
    return parse_trace_lines(text.splitlines())
