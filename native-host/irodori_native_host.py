"""Firefox Native Messaging host for controlling the local Irodori API."""
from __future__ import annotations

import json
import base64
import errno
from contextlib import contextmanager
from datetime import datetime
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

HOST_VERSION = "1.3.0"
EXPECTED_EXTENSION_ID = "irodori-voice-reader@local"
MAX_MESSAGE_BYTES = 96 * 1024 * 1024
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
START_WAIT_SECONDS = 300
DEFAULT_STARTUP_TIMEOUT_SECONDS = 420.0
DEFAULT_STARTUP_POLL_INTERVAL_SECONDS = 2.0
MAX_DATASET_WAV_BYTES = 64 * 1024 * 1024
MAX_DATASET_TEXT_BYTES = 4 * 1024 * 1024
ASYNC_START_INITIAL_POLL_SECONDS = 1.5
ASYNC_START_POLL_INTERVAL_SECONDS = 0.05
ASYNC_START_LAUNCHING_GRACE_SECONDS = 15.0
ASYNC_START_FAILURE_RETRY_SECONDS = 5.0
ENGINE_OPERATION_LOCK_TIMEOUT_SECONDS = 12.0
ENGINE_CANCEL_WAIT_SECONDS = 8.0
ENGINE_DEFINITIONS = {
    "standard": {
        "label": "通常版",
        "api_health_url": "http://127.0.0.1:8765/health",
        "backend_health_url": "http://127.0.0.1:8091/health",
        "start_script": "start_kurisu_voice_api.ps1",
        "stop_api_script": "stop_kurisu_voice_api.ps1",
        "stop_backend_script": "stop_audio_cpp_shared.ps1",
    },
    "voice_stable": {
        "label": "声質安定版",
        # Logical mode only.  The actual HTTP entrance is shared with the
        # normal mode; the request body selects the model inside the API.
        "api_health_url": "http://127.0.0.1:8765/health",
        "backend_health_url": "http://127.0.0.1:8091/health",
        "start_script": "start_kurisu_voice_api.ps1",
        "stop_api_script": "stop_kurisu_voice_api.ps1",
        "stop_backend_script": "stop_audio_cpp_shared.ps1",
    },
}


def log_event(event: str, **fields) -> None:
    """Append a compact diagnostic without request text, voice data, or secrets."""
    try:
        record = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "pid": os.getpid(),
            "event": str(event),
            **fields,
        }
        with Path(__file__).with_name("native_host.log").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass


ISOLATED_ENVIRONMENT = False


def _native_log_dir() -> Path:
    if ISOLATED_ENVIRONMENT:
        return Path(__file__).with_name("logs")
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "IrodoriVoiceReader" / "NativeHost" / "logs"
    return Path(__file__).with_name("logs")


def _timing_log_dir() -> Path:
    if ISOLATED_ENVIRONMENT:
        return Path(__file__).with_name("timing_logs")
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "IrodoriVoiceReader" / "TimingLogs"
    return Path(__file__).with_name("timing_logs")


def _safe_timing_session_id(value: object) -> str:
    text = re.sub(r"[^0-9A-Za-z_-]", "_", str(value or "").strip())
    return text[:96] or f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:12]}"


@contextmanager
def _timing_file_lock(lock_path: Path):
    """Lock one byte so separate Native Host processes append whole JSONL lines."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _timing_record(message: dict) -> dict:
    record = message.get("event")
    if not isinstance(record, dict):
        raise ValueError("計測イベントが不正です。")
    blocked = {"text", "transcript", "original_text", "wav_base64", "database64", "base64", "audio", "blob", "body", "content"}
    clean = {}
    for key, value in record.items():
        name = str(key)
        lowered = name.casefold()
        if lowered in blocked or any(token in lowered for token in ("base64", "transcript", "wav_data")):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[name] = value
    if not clean.get("event"):
        raise ValueError("計測イベント名がありません。")
    encoded = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 128 * 1024:
        raise ValueError("計測イベントが大きすぎます。")
    return clean


def timing_log_append(message: dict) -> dict:
    record = _timing_record(message)
    session_id = _safe_timing_session_id(message.get("session_id"))
    directory = _timing_log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"timing-{session_id}.jsonl"
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _timing_file_lock(path.with_suffix(".lock")):
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
            stream.flush()
    return {"ok": True, "path": str(path), "fileName": path.name}


def open_timing_log_folder() -> dict:
    directory = _timing_log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        explorer = Path(os.environ.get("WINDIR", r"C:\Windows")) / "explorer.exe"
        executable = str(explorer) if explorer.is_file() else "explorer.exe"
        process = None
        launch_error = None
        # Firefox Native Messaging hosts can run in a short-lived Job.  A
        # plain CREATE_NO_WINDOW child is then closed with the host before the
        # Explorer window becomes visible.  Prefer a detached, breakaway
        # Explorer and retain compatibility with machines that reject one of
        # the flags.
        for flags in (
            DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
            DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB,
            CREATE_NO_WINDOW,
        ):
            try:
                process = subprocess.Popen(
                    [executable, str(directory)], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=flags, close_fds=True,
                )
                break
            except OSError as error:
                launch_error = error
        if process is None:
            log_event("timing_folder_open_error", path=str(directory), error=str(launch_error))
            raise RuntimeError(f"計測ログフォルダーを開けませんでした: {launch_error}") from launch_error
        log_event("timing_folder_opened", path=str(directory), pid=process.pid)
    else:
        process = subprocess.Popen(["xdg-open", str(directory)], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log_event("timing_folder_opened", path=str(directory), pid=process.pid)
    return {"ok": True, "path": str(directory), "directory": str(directory), "pid": process.pid}


def _async_state_path(engine: str) -> Path:
    return _native_log_dir() / f"engine_{engine}_async.json"


def _engine_operation_lock_path() -> Path:
    return _native_log_dir() / "engine-operation.lock"


def _engine_intent_path() -> Path:
    return _native_log_dir() / "engine-intent.json"


def _engine_worker_state_path() -> Path:
    return _native_log_dir() / "engine-transition-worker.json"


def _selected_engine_path() -> Path:
    return _native_log_dir() / "selected-engine.json"


def _read_selected_engine(default: str = "standard") -> str:
    value = _read_json_file(_selected_engine_path()) or {}
    try:
        return _engine_name(value.get("engine") or default)
    except ValueError:
        return default


def _write_selected_engine(engine: str, request_token: str = "") -> None:
    selected = _engine_name(engine)
    value = {
        "layout": "unified",
        "engine": selected,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if request_token:
        value["request_token"] = str(request_token)
    _write_json_file(_selected_engine_path(), value)


@contextmanager
def _engine_operation_lock():
    """Serialize cross-process engine operations without keeping a host alive."""
    path = _engine_operation_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0)
    handle.write(b"0")
    handle.flush()
    locked = False
    deadline = time.monotonic() + ENGINE_OPERATION_LOCK_TIMEOUT_SECONDS
    try:
        if os.name == "nt":
            import msvcrt

            while time.monotonic() < deadline:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl

            while time.monotonic() < deadline:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError:
                    time.sleep(0.05)
        if not locked:
            raise TimeoutError("別のIrodori engine切替処理が終了していません。")
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _spawn_engine_transition_worker(config: dict, *, restart_count: int | None = None) -> dict:
    """Start one detached coordinator; subsequent requests only replace intent."""
    state_path = _engine_worker_state_path()
    existing = _read_json_file(state_path)
    if existing:
        alive, _ = _process_status(existing.get("pid"))
        if alive:
            return {"pid": existing.get("pid"), "reused": True, "state": existing.get("state")}
    log_dir = _native_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "engine-transition-worker.stdout.log"
    stderr_path = log_dir / "engine-transition-worker.stderr.log"
    process = None
    worker_error = None
    for flags in (
        CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
        CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    ):
        try:
            with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "--engine-transition-worker"],
                    cwd=str(config["root"]), stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    # Native Messaging hosts are short-lived and Firefox may
                    # place them in a Job object.  Break the worker out of
                    # that Job so a request completing does not kill an
                    # in-progress transition.
                    creationflags=flags,
                    close_fds=True, env=_script_environment(config, "standard"),
                )
            break
        except OSError as error:
            worker_error = error
            log_event("engine_transition_worker_breakaway_fallback",
                      error_type=type(error).__name__, error=str(error))
    if process is None:
        log_event("engine_transition_worker_spawn_error", error_type=type(worker_error).__name__, error=str(worker_error))
        raise RuntimeError(f"engine切替workerを起動できませんでした: {worker_error}") from worker_error
    if restart_count is None:
        # A new user request starts a fresh retry budget.  Only the status
        # recovery path passes an explicit non-zero count.
        restart_count = 0
    value = {
        "state": "running",
        "pid": process.pid,
        "restart_count": max(0, int(restart_count)),
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }
    try:
        _write_json_file(state_path, value)
    except OSError:
        pass
    log_event("engine_transition_worker_started", worker_pid=process.pid,
              stdout=str(stdout_path), stderr=str(stderr_path))
    time.sleep(0.05)
    alive, exit_code = _process_status(process.pid)
    if not alive:
        log_event("engine_transition_worker_immediate_exit", worker_pid=process.pid,
                  exit_code=exit_code, restart_count=value["restart_count"])
    return {"pid": process.pid, "reused": False, "state": "running"}


def _write_json_file(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
        last_error = None
        for _ in range(40):
            try:
                os.replace(temporary, path)
                return
            except PermissionError as error:
                last_error = error
                time.sleep(0.05)
        if last_error is not None:
            raise last_error
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _read_json_file(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (FileNotFoundError, OSError, ValueError):
        return None


def _process_status(pid: object) -> tuple[bool, int | None]:
    """Return (alive, exit_code) without keeping a process handle open."""
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False, None
    if value <= 0:
        return False, None
    if os.name == "nt":
        try:
            import ctypes

            access = 0x1000 | 0x00100000  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
            handle = ctypes.windll.kernel32.OpenProcess(access, False, value)
            if not handle:
                return False, None
            exit_code = ctypes.c_ulong()
            try:
                if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True, None
                if exit_code.value == 259:  # STILL_ACTIVE
                    return True, None
                return False, int(exit_code.value)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass
    try:
        os.kill(value, 0)
        return True, None
    except PermissionError:
        return True, None
    except OSError:
        return False, None


def _text_tail(path: Path, limit: int = 1200) -> str:
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    if raw.startswith(b"#< CLIXML") or raw.startswith(b"<Objs"):
        encodings = ("cp932", "utf-8-sig", "utf-16")
    elif raw.startswith((b"\xff\xfe", b"\xfe\xff")) or raw.count(b"\x00") > max(2, len(raw) // 10):
        encodings = ("utf-16", "utf-8-sig", "cp932")
    else:
        encodings = ("utf-8-sig", "cp932", "utf-16")
    for encoding in encodings:
        try:
            value = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\ufffd" not in value:
            return value[-limit:].strip()
    return raw.decode("utf-8", errors="replace")[-limit:].strip()


def _ps_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _async_guard_is_active(value: dict | None) -> bool:
    if not value:
        return False
    state = str(value.get("state") or "")
    if state == "launching":
        try:
            return time.time() - float(value.get("started_epoch", 0)) < ASYNC_START_LAUNCHING_GRACE_SECONDS
        except (TypeError, ValueError):
            return False
    if state != "running":
        return False
    alive, _ = _process_status(value.get("pid"))
    return alive


def _terminate_recorded_process(pid: object, engine: str, role: str, config: dict) -> bool:
    """Terminate only a PID recorded by our detached launcher."""
    alive, _ = _process_status(pid)
    if not alive:
        return False
    try:
        numeric_pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        if os.name == "nt":
            # A saved PID can be reused. Verify the launcher command belongs to
            # this exact root before terminating it; never taskkill a PID tree.
            command = f"""
$item = Get-CimInstance Win32_Process -Filter 'ProcessId = {numeric_pid}' -ErrorAction SilentlyContinue
if ($null -eq $item) {{ exit 0 }}
$line = [string]$item.CommandLine
if ($line -match '-EncodedCommand\\s+([A-Za-z0-9+/=]+)') {{
    try {{ $line = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($Matches[1])) }} catch {{ exit 1 }}
}}
$expected = {_ps_literal(config["root"] / "start_kurisu_voice_api.ps1")}
if ($item.Name -notin @('powershell.exe','pwsh.exe') -or $line.IndexOf($expected, [StringComparison]::OrdinalIgnoreCase) -lt 0) {{ exit 1 }}
Stop-Process -Id $item.ProcessId -Force -ErrorAction Stop
"""
            encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10, creationflags=CREATE_NO_WINDOW, env=_script_environment(config),
            )
            if result.returncode != 0:
                return False
            log_event("async_start_process_cancelled", engine=engine, role=role,
                      pid=numeric_pid, returncode=result.returncode)
        else:
            os.kill(numeric_pid, 15)
            log_event("async_start_process_cancelled", engine=engine, role=role, pid=numeric_pid)
    except (OSError, subprocess.SubprocessError) as error:
        log_event("async_start_process_cancel_error", engine=engine, role=role,
                  pid=numeric_pid, error_type=type(error).__name__, error=str(error))
        return False
    deadline = time.monotonic() + ENGINE_CANCEL_WAIT_SECONDS
    while time.monotonic() < deadline:
        if not _process_status(numeric_pid)[0]:
            return True
        time.sleep(0.1)
    return not _process_status(numeric_pid)[0]


def _cancel_async_startup(config: dict, engine: str, reason: str) -> dict:
    """Cancel a detached startup before stopping or replacing an engine."""
    engine = _engine_name(engine)
    path = _async_state_path(engine)
    value = _read_json_file(path)
    if not value:
        return {"engine": engine, "cancelled": False}
    pids = []
    for key, role in (("wrapper_pid", "wrapper"), ("pid", "script")):
        pid = value.get(key)
        if pid and pid not in [item[0] for item in pids]:
            pids.append((pid, role))
    cancelled = False
    for pid, role in pids:
        cancelled = _terminate_recorded_process(pid, engine, role, config) or cancelled
    state = str(value.get("state") or "")
    if cancelled or state in {"launching", "running", "launch_complete", "ready"}:
        value.update({
            "state": "cancelled",
            "cancel_reason": str(reason),
            "cancelled_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
        try:
            _write_json_file(path, value)
        except OSError as error:
            log_event("async_start_cancel_state_error", engine=engine, error_type=type(error).__name__, error=str(error))
        log_event("async_start_cancelled", engine=engine, reason=str(reason),
                  pid=value.get("pid"), wrapper_pid=value.get("wrapper_pid"), state=state,
                  state_file=str(path))
    return {"engine": engine, "cancelled": cancelled or state in {"launching", "running", "launch_complete", "ready"},
            "pid": value.get("pid"), "wrapperPid": value.get("wrapper_pid"), "stateFile": str(path)}


def _configured_backend_executable(config: dict, engine: str) -> Path:
    configured = "audio-cpp\\bin\\audiocpp_server.exe"
    runtime_path = config["root"] / "config" / "runtime.json"
    try:
        runtime = json.loads(runtime_path.read_text(encoding="utf-8-sig"))
        runtime_executable = (
            str(runtime.get("audio_cpp_shared_executable") or "").strip()
            or str(runtime.get("audio_cpp_executable") or "").strip()
        )
        if runtime_executable:
            configured = runtime_executable
    except (OSError, ValueError, TypeError):
        pass
    path = Path(configured)
    return path.resolve() if path.is_absolute() else (config["root"] / path).resolve()


def _stop_owned_engine_processes(config: dict, engine: str) -> list[int]:
    """Stop only API/backend processes whose command or executable belongs to this checkout.

    The fixed start scripts can spawn a child immediately before their launcher is
    cancelled, leaving no PID file for the normal stop script to consume.  A
    second, path-checked sweep closes that race without touching unrelated Python
    or audio.cpp processes.
    """
    if os.name != "nt":
        return []
    engine = _engine_name(engine)
    root = _ps_literal(config["root"])
    api_names = "@(" + ",".join(_ps_literal(name) for name in ("kurisu_voice_api.py", "kurisu_voice_stable_api.py")) + ")"
    backend = _ps_literal(_configured_backend_executable(config, engine))
    api_script = _ps_literal(config["root"] / "kurisu_voice_api.py")
    api_python = _ps_literal(config["root"] / ".venv" / "Scripts" / "python.exe")
    backend_config = _ps_literal(config["root"] / "config" / "audio_cpp_server.shared.json")
    command = f"""
$root = [IO.Path]::GetFullPath({root})
$apiNames = {api_names}
$apiScript = {api_script}
$apiPython = {api_python}
$backendConfig = {backend_config}
$backend = [IO.Path]::GetFullPath({backend})
$items = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {{
    $line = [string]$_.CommandLine
    $apiMatch = $_.Name -in @('python.exe','pythonw.exe') -and $line.IndexOf($apiScript, [StringComparison]::OrdinalIgnoreCase) -ge 0
    $exeMatch = $_.ExecutablePath -and ([IO.Path]::GetFullPath([string]$_.ExecutablePath) -ieq $backend) -and $line.IndexOf($backendConfig, [StringComparison]::OrdinalIgnoreCase) -ge 0
    $apiMatch -or $exeMatch
}})
foreach ($item in $items) {{
    if ($item.ProcessId -and $item.ProcessId -ne $PID) {{
        Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Output ([string]$item.ProcessId)
    }}
}}
"""
    encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    powershell = shutil.which("powershell.exe") or "powershell.exe"
    try:
        result = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=15, creationflags=CREATE_NO_WINDOW, env=_script_environment(config),
        )
    except (OSError, subprocess.SubprocessError) as error:
        log_event("owned_process_sweep_error", engine=engine, error_type=type(error).__name__, error=str(error))
        return []
    pids = []
    for line in result.stdout.decode("ascii", errors="ignore").splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid > 0 and pid not in pids:
            pids.append(pid)
    if pids:
        log_event("owned_engine_processes_stopped", engine=engine, pids=pids)
    return pids


def _async_start_status(config: dict, engine: str, health_state: dict | None = None) -> dict:
    path = _async_state_path(engine)
    value = _read_json_file(path)
    if not value:
        return {}
    if health_state and health_state.get("running"):
        if value.get("state") != "ready":
            value.update({"state": "ready", "ready_at": datetime.now().astimezone().isoformat(timespec="seconds")})
            try:
                _write_json_file(path, value)
            except OSError:
                pass
        return {
            "state": "ready",
            "pid": value.get("pid"),
            "stdout": value.get("stdout"),
            "stderr": value.get("stderr"),
            "launcherStdout": value.get("launcher_stdout"),
            "launcherStderr": value.get("launcher_stderr"),
        }
    state = str(value.get("state") or "")
    if state == "launching":
        try:
            launch_age = time.time() - float(value.get("started_epoch", 0))
        except (TypeError, ValueError):
            launch_age = ASYNC_START_LAUNCHING_GRACE_SECONDS + 1
        if launch_age >= ASYNC_START_LAUNCHING_GRACE_SECONDS:
            error = "PowerShellラッパーが起動PIDを返さないまま終了しました。"
            value.update({"state": "failed", "ended_at": datetime.now().astimezone().isoformat(timespec="seconds"), "error": error})
            try:
                _write_json_file(path, value)
            except OSError:
                pass
            log_event("async_start_launcher_stalled", engine=engine, error=error, state_file=str(path))
            state = "failed"
    if state == "running":
        alive, exit_code = _process_status(value.get("pid"))
        if not alive:
            if exit_code in (0, None) and not (value.get("stderr") and _text_tail(Path(value["stderr"]))):
                # The fixed PowerShell launcher intentionally exits after it
                # has spawned the resident API/backend.  Health may become OK
                # a little later, so this is not a startup failure.
                value.update({"state": "launch_complete", "script_exit_code": 0,
                              "launch_complete_at": datetime.now().astimezone().isoformat(timespec="seconds")})
                try:
                    _write_json_file(path, value)
                except OSError:
                    pass
                state = "launch_complete"
            else:
                stderr_tail = _text_tail(Path(value["stderr"])) if value.get("stderr") else ""
                error = f"{value.get('script', '起動スクリプト')} が終了しました。exit code={exit_code if exit_code is not None else '不明'}"
                if stderr_tail:
                    error += f" / stderr: {stderr_tail}"
                value.update({"state": "failed", "exit_code": exit_code, "ended_at": datetime.now().astimezone().isoformat(timespec="seconds"), "error": error})
                try:
                    _write_json_file(path, value)
                except OSError:
                    pass
                log_event("async_start_child_exit", engine=engine, pid=value.get("pid"), exit_code=exit_code,
                          stderr=value.get("stderr"), error=error)
                state = "failed"
    if state == "failed":
        return {
            "state": "failed",
            "pid": value.get("pid"),
            "exitCode": value.get("exit_code"),
            "error": value.get("error") or "非同期起動プロセスが終了しました。",
            "stdout": value.get("stdout"),
            "stderr": value.get("stderr"),
            "launcherStdout": value.get("launcher_stdout"),
            "launcherStderr": value.get("launcher_stderr"),
        }
    if state in {"launching", "running", "launch_complete"}:
        return {
            "state": state,
            "pid": value.get("pid"),
            "stdout": value.get("stdout"),
            "stderr": value.get("stderr"),
            "launcherStdout": value.get("launcher_stdout"),
            "launcherStderr": value.get("launcher_stderr"),
        }
    return {}


def read_message() -> dict | None:
    header = sys.stdin.buffer.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise ValueError("Native Messagingヘッダーが不正です。")
    (length,) = struct.unpack("<I", header)
    if length < 2 or length > MAX_MESSAGE_BYTES:
        raise ValueError("Native Messagingメッセージ長が不正です。")
    body = sys.stdin.buffer.read(length)
    if len(body) != length:
        raise ValueError("Native Messagingメッセージが途中で終了しました。")
    value = json.loads(body.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSONオブジェクトが必要です。")
    return value


def write_message(value: dict) -> None:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(body)))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def load_config() -> dict:
    global ISOLATED_ENVIRONMENT
    path = Path(__file__).with_name("config.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    ISOLATED_ENVIRONMENT = bool(value.get("isolated_environment", False))
    if ISOLATED_ENVIRONMENT:
        for key in ("health_url", "backend_health_url"):
            if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}/health", str(value.get(key, ""))):
                raise ValueError("独立環境の接続先は専用ループバックURLが必要です。")
        for definition in ENGINE_DEFINITIONS.values():
            definition["api_health_url"] = value["health_url"]
            definition["backend_health_url"] = value["backend_health_url"]
    root = Path(value["irodori_root"]).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Irodoriフォルダーがありません: {root}")
    return {
        "root": root,
        "chrome_extension_id": value.get("chrome_extension_id", "aeaichhflmacmifblidbpojgfemjijlb"),
        "firefox_extension_id": value.get("firefox_extension_id", EXPECTED_EXTENSION_ID),
        "health_url": value.get("health_url", "http://127.0.0.1:8765/health"),
        "backend_health_url": value.get("backend_health_url", "http://127.0.0.1:8091/health"),
        "start_task": value.get("start_task", ""),
        "startup_timeout_seconds": max(1.0, float(value.get("startup_timeout_seconds", DEFAULT_STARTUP_TIMEOUT_SECONDS))),
        "startup_poll_interval_seconds": max(0.25, float(value.get("startup_poll_interval_seconds", DEFAULT_STARTUP_POLL_INTERVAL_SECONDS))),
    }


def _http_health(url: str) -> dict:
    try:
        request = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
        with urllib.request.urlopen(request, timeout=2.5) as response:
            value = json.loads(response.read().decode("utf-8"))
        return {"running": value.get("status") == "ok", "health": value}
    except (OSError, ValueError, urllib.error.URLError):
        return {"running": False, "health": None}


def _engine_name(value: object) -> str:
    engine = str(value or "standard").strip().lower().replace("-", "_")
    engine = {"normal": "standard", "default": "standard", "stable": "voice_stable", "safe": "voice_stable"}.get(engine, engine)
    if engine not in ENGINE_DEFINITIONS:
        raise ValueError("未対応の生成エンジンです。")
    return engine


def engine_health(config: dict, engine: str, api: dict | None = None, backend: dict | None = None) -> dict:
    engine = _engine_name(engine)
    definition = ENGINE_DEFINITIONS[engine]
    # Both logical modes intentionally report the same shared process.  This
    # keeps the UI's normal/safe choices while preventing a second HTTP port
    # or a stop/start race during a mode switch.
    api_url = definition["api_health_url"]
    if api is None:
        api = _http_health(f"{api_url}?engine={engine}")
    backend = backend if backend is not None else _http_health(definition["backend_health_url"])
    selected = _read_selected_engine()
    return {
        "engine": engine,
        "label": definition["label"],
        "running": api["running"] and backend["running"],
        "api": api,
        "backend": backend,
        "endpoint": definition["api_health_url"].rsplit("/", 1)[0],
        "shared": True,
        "selected": selected == engine,
    }


def health(config: dict) -> dict:
    return _http_health(config["health_url"])


def backend_health(config: dict) -> dict:
    return _http_health(config["backend_health_url"])


def _script_environment(config: dict, engine: str = "standard") -> dict:
    env = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    return env


def run_script(config: dict, filename: str, timeout: int, engine: str = "standard") -> None:
    script = (config["root"] / filename).resolve()
    if script.parent != config["root"] or not script.is_file():
        raise FileNotFoundError(f"固定スクリプトがありません: {script}")
    env = _script_environment(config, engine)
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=str(config["root"]),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{filename} が終了コード {result.returncode} で失敗しました。")


def wait_for_state(config: dict, expected: bool, seconds: float) -> dict:
    deadline = time.monotonic() + seconds
    last = health(config)
    while last["running"] != expected and time.monotonic() < deadline:
        time.sleep(0.25)
        last = health(config)
    return last


def _acquire_async_guard(config: dict, engine: str, filename: str) -> tuple[bool, dict | None, Path]:
    path = _async_state_path(engine)
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        existing = _read_json_file(path)
        if existing and _async_guard_is_active(existing):
            return False, existing, path
        if existing:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                return False, existing, path
        value = {
            "state": "launching",
            "engine": engine,
            "script": filename,
            "started_epoch": time.time(),
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "launcher_pid": os.getpid(),
        }
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(descriptor, (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            finally:
                os.close(descriptor)
            return True, value, path
        except FileExistsError:
            continue
    existing = _read_json_file(path)
    return False, existing, path


def _launch_script_async(config: dict, filename: str, engine: str = "standard", *, request_token: str = "") -> dict:
    script = (config["root"] / filename).resolve()
    if script.parent != config["root"] or not script.is_file():
        raise FileNotFoundError(f"固定スクリプトがありません: {script}")
    acquired, existing, state_path = _acquire_async_guard(config, engine, filename)
    if not acquired:
        if existing and _async_guard_is_active(existing):
            log_event("async_start_duplicate_ignored", engine=engine, pid=existing.get("pid"),
                      state=existing.get("state"), state_file=str(state_path))
            return {"duplicate": True, **existing, "state_file": str(state_path)}
        raise RuntimeError(f"{ENGINE_DEFINITIONS[engine]['label']}の非同期起動状態を確保できませんでした。")

    log_dir = _native_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    prefix = log_dir / f"start-{engine}-{stamp}-{os.getpid()}"
    stdout_path = prefix.with_suffix(".stdout.log")
    stderr_path = prefix.with_suffix(".stderr.log")
    launcher_stdout_path = prefix.with_suffix(".launcher.stdout.log")
    launcher_stderr_path = prefix.with_suffix(".launcher.stderr.log")
    state = dict(existing or {})
    state.update({"state": "launching", "engine": engine, "script": filename,
                  "stdout": str(stdout_path), "stderr": str(stderr_path),
                  "launcher_stdout": str(launcher_stdout_path), "launcher_stderr": str(launcher_stderr_path),
                  "state_file": str(state_path), "request_token": str(request_token or "")})
    _write_json_file(state_path, state)
    env = _script_environment(config, engine)
    powershell = shutil.which("powershell.exe") or "powershell.exe"
    command = (
        "$OutputEncoding = New-Object System.Text.UTF8Encoding($false); "
        "[Console]::OutputEncoding = $OutputEncoding; "
        f"$p = Start-Process -FilePath {_ps_literal(powershell)} "
        f"-ArgumentList @('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',{_ps_literal(chr(34) + str(script) + chr(34))}) "
        f"-WorkingDirectory {_ps_literal(config['root'])} "
        f"-RedirectStandardOutput {_ps_literal(stdout_path)} "
        f"-RedirectStandardError {_ps_literal(stderr_path)} -WindowStyle Hidden -PassThru; "
        "[Console]::Out.WriteLine([string]$p.Id)"
    )
    encoded_command = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    wrapper_args = [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded_command]
    wrapper = None
    wrapper_error = None
    for flags in (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB, CREATE_NO_WINDOW):
        try:
            with launcher_stdout_path.open("wb") as launcher_stdout, launcher_stderr_path.open("wb") as launcher_stderr:
                wrapper = subprocess.Popen(
                    wrapper_args,
                    cwd=str(config["root"]), stdin=subprocess.DEVNULL,
                    stdout=launcher_stdout, stderr=launcher_stderr,
                    creationflags=flags, close_fds=True, env=env,
                )
            state["wrapper_pid"] = wrapper.pid
            break
        except OSError as error:
            wrapper_error = error
            log_event("async_start_breakaway_fallback", engine=engine, error_type=type(error).__name__, error=str(error))
    if wrapper is None:
        state.update({"state": "failed", "error": f"PowerShellラッパーを起動できませんでした: {wrapper_error}"})
        _write_json_file(state_path, state)
        log_event("async_start_launcher_error", engine=engine, error=str(wrapper_error), state_file=str(state_path))
        raise RuntimeError(state["error"])
    launcher_deadline = time.monotonic() + 10.0
    while wrapper.poll() is None and time.monotonic() < launcher_deadline:
        time.sleep(ASYNC_START_POLL_INTERVAL_SECONDS)
    if wrapper.poll() is None:
        wrapper.kill()
        wrapper.wait()
        state.update({"state": "failed", "error": "PowerShellラッパーが10秒以内に終了しませんでした。"})
        _write_json_file(state_path, state)
        log_event("async_start_launcher_timeout", engine=engine, wrapper_pid=wrapper.pid, state_file=str(state_path))
        raise RuntimeError(state["error"])
    wrapper_err_text = _text_tail(launcher_stderr_path)
    if wrapper.returncode != 0:
        detail = wrapper_err_text[-1200:] if wrapper_err_text else ""
        error = f"PowerShellラッパーが終了コード {wrapper.returncode} で失敗しました。"
        if detail:
            error += f" stderr: {detail}"
        state.update({"state": "failed", "wrapper_exit_code": wrapper.returncode, "error": error})
        _write_json_file(state_path, state)
        log_event("async_start_launcher_exit", engine=engine, wrapper_pid=wrapper.pid,
                  exit_code=wrapper.returncode, stderr=detail, state_file=str(state_path))
        raise RuntimeError(error)
    pid_text = _text_tail(launcher_stdout_path, limit=2000).splitlines()
    child_pid = None
    for line in reversed(pid_text):
        try:
            child_pid = int(line.strip())
            break
        except ValueError:
            continue
    if not child_pid:
        detail = wrapper_err_text[-1200:] if wrapper_err_text else "PowerShell子PIDが返されませんでした。"
        state.update({"state": "failed", "wrapper_exit_code": wrapper.returncode, "error": detail})
        _write_json_file(state_path, state)
        log_event("async_start_pid_missing", engine=engine, wrapper_pid=wrapper.pid, stderr=detail, state_file=str(state_path))
        raise RuntimeError(detail)
    state.update({"state": "running", "pid": child_pid, "wrapper_exit_code": wrapper.returncode})
    _write_json_file(state_path, state)
    log_event("async_start_child_started", engine=engine, pid=child_pid, wrapper_pid=wrapper.pid,
              script=filename, stdout=str(stdout_path), stderr=str(stderr_path), state_file=str(state_path))
    deadline = time.monotonic() + ASYNC_START_INITIAL_POLL_SECONDS
    while time.monotonic() < deadline:
        alive, exit_code = _process_status(child_pid)
        if not alive:
            health_state = engine_health(config, engine)
            if health_state["running"]:
                state.update({"state": "ready", "exit_code": exit_code})
                _write_json_file(state_path, state)
                return state
            if exit_code in (0, None) and not _text_tail(stderr_path):
                # The launcher can finish successfully just after spawning
                # the resident API.  Keep polling health instead of turning a
                # normal launcher exit into a false startup failure.
                state.update({"state": "launch_complete", "script_exit_code": 0,
                              "launch_complete_at": datetime.now().astimezone().isoformat(timespec="seconds")})
                _write_json_file(state_path, state)
                log_event("async_start_launcher_complete_waiting_health", engine=engine, pid=child_pid,
                          state_file=str(state_path))
                return state
            stderr_tail = _text_tail(stderr_path)
            error = f"{filename} が終了しました。exit code={exit_code if exit_code is not None else '不明'}"
            if stderr_tail:
                error += f" / stderr: {stderr_tail}"
            state.update({"state": "failed", "exit_code": exit_code,
                          "ended_at": datetime.now().astimezone().isoformat(timespec="seconds"), "error": error})
            _write_json_file(state_path, state)
            log_event("async_start_child_exit", engine=engine, pid=child_pid, exit_code=exit_code,
                      stderr=str(stderr_path), error=error, state_file=str(state_path))
            raise RuntimeError(error)
        time.sleep(ASYNC_START_POLL_INTERVAL_SECONDS)
    return state


def _spawn_startup_monitor() -> None:
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--monitor-startup"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS, close_fds=True,
    )


def monitor_startup(config: dict) -> int:
    started = time.monotonic()
    timeout = config["startup_timeout_seconds"]
    interval = config["startup_poll_interval_seconds"]
    previous = None
    while True:
        api = health(config)
        backend = backend_health(config)
        elapsed = round(time.monotonic() - started, 1)
        current = (backend["running"], api["running"])
        if current != previous:
            log_event("startup_health", root=str(config["root"]), elapsed_seconds=elapsed,
                      backend_health=backend["running"], api_health=api["running"])
            previous = current
        if api["running"]:
            log_event("startup_ready", root=str(config["root"]), elapsed_seconds=elapsed)
            return 0
        if elapsed >= timeout:
            log_event("startup_timeout", root=str(config["root"]), elapsed_seconds=elapsed,
                      backend_health=backend["running"], api_health=api["running"])
            return 1
        time.sleep(interval)


def wait_for_engine(config: dict, engine: str, expected: bool, seconds: float) -> dict:
    deadline = time.monotonic() + seconds
    last = engine_health(config, engine)
    while last["running"] != expected and time.monotonic() < deadline:
        time.sleep(0.25)
        last = engine_health(config, engine)
    return last


def _stop_engine_unlocked(config: dict, engine: str) -> dict:
    engine = _engine_name(engine)
    definition = ENGINE_DEFINITIONS[engine]
    cancel_result = _cancel_async_startup(config, engine, "engine_stop")
    before = engine_health(config, engine)
    log_event("engine_stop_requested", engine=engine, api=before["api"]["running"], backend=before["backend"]["running"])
    run_script(config, definition["stop_api_script"], 30, engine)
    run_script(config, definition["stop_backend_script"], 30, engine)
    _stop_owned_engine_processes(config, engine)
    deadline = time.monotonic() + 15
    next_sweep = time.monotonic() + 1.0
    state = engine_health(config, engine)
    while (state["api"]["running"] or state["backend"]["running"]) and time.monotonic() < deadline:
        if time.monotonic() >= next_sweep:
            _stop_owned_engine_processes(config, engine)
            next_sweep = time.monotonic() + 1.0
        time.sleep(0.25)
        state = engine_health(config, engine)
    if state["api"]["running"] or state["backend"]["running"]:
        raise RuntimeError(f"{definition['label']}の停止後もAPIまたはbackendが応答しています。")
    log_event("engine_stopped", engine=engine)
    return {"ok": True, "running": False, "engine": engine, "cancelResult": cancel_result,
            "message": f"{definition['label']}を停止しました。"}


def stop_engine(config: dict, engine: str, *, _lock_held: bool = False) -> dict:
    if _lock_held:
        return _stop_engine_unlocked(config, engine)
    with _engine_operation_lock():
        return _stop_engine_unlocked(config, engine)


def _start_engine_unlocked(config: dict, engine: str) -> dict:
    engine = _engine_name(engine)
    definition = ENGINE_DEFINITIONS[engine]
    _write_selected_engine(engine)
    _cancel_async_startup(config, engine, "sync_start_replaced")
    before = engine_health(config, engine)
    if before["running"]:
        log_event("engine_start_already_ready", engine=engine)
        return {"ok": True, **before, "message": f"{definition['label']}は既に稼働中です。", "restarted": False}
    log_event("engine_start_requested", engine=engine, root=str(config["root"]))
    script = (config["root"] / definition["start_script"]).resolve()
    if script.parent != config["root"] or not script.is_file():
        raise FileNotFoundError(f"固定スクリプトがありません: {script}")
    process = subprocess.Popen(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=str(config["root"]), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW, env=_script_environment(config, engine),
    )
    deadline = time.monotonic() + START_WAIT_SECONDS
    state = engine_health(config, engine)
    while not state["running"] and time.monotonic() < deadline:
        if process.poll() not in (None, 0):
            raise RuntimeError(f"{definition['label']}の起動スクリプトが終了コード {process.returncode} で失敗しました。")
        time.sleep(0.5)
        state = engine_health(config, engine)
    if not state["running"]:
        log_event("engine_start_timeout", engine=engine, seconds=START_WAIT_SECONDS)
        raise RuntimeError(f"{definition['label']}起動後のヘルスチェックに失敗しました。")
    log_event("engine_ready", engine=engine, endpoint=state["endpoint"])
    return {"ok": True, **state, "message": f"{definition['label']}を起動しました。", "restarted": True}


def start_engine(config: dict, engine: str) -> dict:
    with _engine_operation_lock():
        return _start_engine_unlocked(config, engine)


def _start_engine_async_unlocked(config: dict, engine: str, *, request_token: str = "", reason: str = "") -> dict:
    """Stop the opposite engine and launch the selected engine without waiting for health.

    Firefox polls the ``status`` action while the fixed start script loads the
    backend.  Keeping the launch detached avoids holding a Native Messaging
    request (and the sidebar) for the full model-load timeout.
    """
    engine = _engine_name(engine)
    definition = ENGINE_DEFINITIONS[engine]
    opposite = "voice_stable" if engine == "standard" else "standard"
    intent = _read_json_file(_engine_intent_path())
    intent_state = str((intent or {}).get("state") or "")
    intent_engine = str((intent or {}).get("engine") or "")
    intent_token = str((intent or {}).get("request_token") or "")
    if request_token and intent_token and request_token != intent_token:
        raise RuntimeError("新しいengine切替要求が優先されています。")
    if not request_token and reason in {"extension_initialize", "startup"} and intent_engine and intent_engine != engine and intent_state in {"switching", "starting", "ready"}:
        log_event("async_start_stale_request_ignored", engine=engine, reason=reason,
                  requested_engine=intent_engine, intent_state=intent_state)
        raise RuntimeError("古いengine起動要求を破棄しました。")
    before = {name: engine_health(config, name) for name in ENGINE_DEFINITIONS}
    stop_result = None
    opposite_cancel = _cancel_async_startup(config, opposite, "engine_start_async")
    after_cancel = engine_health(config, opposite)
    if (opposite_cancel.get("cancelled")
            or before[opposite]["api"]["running"]
            or before[opposite]["backend"]["running"]
            or after_cancel["api"]["running"]
            or after_cancel["backend"]["running"]):
        stop_result = _stop_engine_unlocked(config, opposite)
    selected = engine_health(config, engine)
    if selected["running"]:
        _async_start_status(config, engine, selected)
        log_event("engine_start_async_already_ready", engine=engine)
        return {"ok": True, **selected, "message": f"{definition['label']}は既に稼働中です。", "restarted": False,
                "starting": False, "stopResult": stop_result}
    previous_start = _async_start_status(config, engine, selected)
    if previous_start.get("state") == "failed":
        try:
            failure_age = time.time() - float((_read_json_file(_async_state_path(engine)) or {}).get("started_epoch", 0))
        except (TypeError, ValueError):
            failure_age = ASYNC_START_FAILURE_RETRY_SECONDS + 1
        if failure_age < ASYNC_START_FAILURE_RETRY_SECONDS:
            log_event("async_start_recent_failure_reused", engine=engine, error=previous_start.get("error"))
            raise RuntimeError(previous_start.get("error") or f"{definition['label']}の起動に失敗しました。")
    log_event("engine_start_async_requested", engine=engine, root=str(config["root"]),
              request_token=request_token, reason=reason)
    launch = _launch_script_async(config, definition["start_script"], engine, request_token=request_token)
    if launch.get("duplicate"):
        return {
            "ok": True,
            "engine": engine,
            "label": definition["label"],
            "running": False,
            "starting": True,
            "duplicate": True,
            "startupPid": launch.get("pid"),
            "startupState": launch.get("state"),
            "startupLog": {"stdout": launch.get("stdout"), "stderr": launch.get("stderr"),
                            "launcherStdout": launch.get("launcherStdout") or launch.get("launcher_stdout"),
                            "launcherStderr": launch.get("launcherStderr") or launch.get("launcher_stderr")},
            "endpoint": definition["api_health_url"].rsplit("/", 1)[0],
            "stopResult": stop_result,
            "startupTimeoutMs": int(config["startup_timeout_seconds"] * 1000),
            "pollIntervalMs": int(config["startup_poll_interval_seconds"] * 1000),
            "message": f"{definition['label']}は既に起動処理中です。既存のPIDを監視します。",
        }
    return {
        "ok": True,
        "engine": engine,
        "label": definition["label"],
        "running": False,
        "starting": True,
        "startupPid": launch.get("pid"),
        "startupState": launch.get("state"),
        "startupLog": {"stdout": launch.get("stdout"), "stderr": launch.get("stderr"),
                        "launcherStdout": launch.get("launcherStdout") or launch.get("launcher_stdout"),
                        "launcherStderr": launch.get("launcherStderr") or launch.get("launcher_stderr")},
        "endpoint": definition["api_health_url"].rsplit("/", 1)[0],
        "stopResult": stop_result,
        "startupTimeoutMs": int(config["startup_timeout_seconds"] * 1000),
        "pollIntervalMs": int(config["startup_poll_interval_seconds"] * 1000),
        "message": f"{definition['label']}の起動を開始しました。ヘルスチェックを継続します。",
    }


def _start_unified_engine_async_unlocked(config: dict, requested: str, *, request_token: str = "", reason: str = "") -> dict:
    """Start the one API entrance without stopping/restarting for a mode switch."""
    requested = _engine_name(requested)
    caller_token = str(request_token or "")
    request_token = caller_token or f"native-{os.getpid()}-{time.time_ns()}"
    intent = _read_json_file(_engine_intent_path()) or {}
    intent_engine = str(intent.get("engine") or "")
    intent_state = str(intent.get("state") or "")
    if (not caller_token and reason in {"extension_initialize", "startup"}
            and intent.get("layout") == "unified"
            and intent_engine in ENGINE_DEFINITIONS
            and intent_engine != requested
            and intent_state in {"switching", "starting", "ready"}):
        log_event("unified_async_start_stale_request_ignored", engine=requested, reason=reason,
                  requested_engine=intent_engine, intent_state=intent_state)
        raise RuntimeError("古いengine起動要求を破棄しました。")
    _write_selected_engine(requested, request_token)
    common = engine_health(config, "standard")
    selected = engine_health(config, requested, common["api"], common["backend"])
    definition = ENGINE_DEFINITIONS[requested]
    if common["running"]:
        _write_json_file(_engine_intent_path(), {
            "layout": "unified", "state": "ready", "engine": requested,
            "request_token": request_token,
            "ready_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
        log_event("unified_engine_already_ready", engine=requested, reason=reason)
        return {"ok": True, **selected, "engine": requested, "currentEngine": requested,
                "running": True, "starting": False, "restarted": False,
                "requestToken": request_token, "message": f"{definition['label']}は既に稼働中です。"}

    previous = _async_start_status(config, "standard", common)
    if previous.get("state") in {"launching", "running", "launch_complete"}:
        intent = {
            "layout": "unified", "state": "starting", "engine": requested,
            "request_token": request_token,
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "startup_pid": previous.get("pid"), "startup_state": previous.get("state"),
        }
        _write_json_file(_engine_intent_path(), intent)
        log_event("unified_engine_start_reused", engine=requested, request_token=request_token,
                  startup_pid=previous.get("pid"), startup_state=previous.get("state"))
        return {
            "ok": True, "engine": requested, "label": definition["label"],
            "running": False, "starting": True, "duplicate": True,
            "startupPid": previous.get("pid"), "startupState": previous.get("state"),
            "startupLog": {"stdout": previous.get("stdout"), "stderr": previous.get("stderr"),
                            "launcherStdout": previous.get("launcherStdout"), "launcherStderr": previous.get("launcherStderr")},
            "endpoint": definition["api_health_url"].rsplit("/", 1)[0],
            "requestToken": request_token,
            "startupTimeoutMs": int(config["startup_timeout_seconds"] * 1000),
            "pollIntervalMs": int(config["startup_poll_interval_seconds"] * 1000),
            "message": f"{definition['label']}は既に起動処理中です。既存のPIDを監視します。",
        }

    log_event("unified_engine_starting", engine=requested, root=str(config["root"]),
              request_token=request_token, reason=reason)
    try:
        launch = _launch_script_async(config, "start_kurisu_voice_api.ps1", "standard", request_token=request_token)
    except Exception as error:
        _write_json_file(_engine_intent_path(), {
            "layout": "unified", "state": "failed", "engine": requested,
            "request_token": request_token, "error": str(error),
            "failed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
        raise
    intent = {
        "layout": "unified", "state": "starting", "engine": requested,
        "request_token": request_token,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "startup_pid": launch.get("pid"), "startup_state": launch.get("state") or "starting",
    }
    if launch.get("state") == "ready":
        intent.update({"state": "ready", "ready_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    _write_json_file(_engine_intent_path(), intent)
    return {
        "ok": True, "engine": requested, "label": definition["label"],
        "running": False, "starting": True,
        "startupPid": launch.get("pid"), "startupState": launch.get("state"),
        "startupLog": {"stdout": launch.get("stdout"), "stderr": launch.get("stderr"),
                        "launcherStdout": launch.get("launcher_stdout"), "launcherStderr": launch.get("launcher_stderr")},
        "endpoint": definition["api_health_url"].rsplit("/", 1)[0],
        "requestToken": request_token,
        "startupTimeoutMs": int(config["startup_timeout_seconds"] * 1000),
        "pollIntervalMs": int(config["startup_poll_interval_seconds"] * 1000),
        "message": f"{definition['label']}の起動を開始しました。ヘルスチェックを継続します。",
    }


def start_engine_async(config: dict, engine: str, *, request_token: str = "", reason: str = "") -> dict:
    with _engine_operation_lock():
        return _start_unified_engine_async_unlocked(config, engine, request_token=request_token, reason=reason)


def _switch_engine_operation_unlocked(config: dict, requested: str, request_token: str) -> dict:
    """Perform one intent while the engine operation lock is held."""
    requested = _engine_name(requested)
    opposite = "voice_stable" if requested == "standard" else "standard"
    before = {name: engine_health(config, name) for name in ENGINE_DEFINITIONS}
    pending_intent = _read_json_file(_engine_intent_path()) or {}
    log_event("engine_switch_async_requested", requested_engine=requested,
              request_token=request_token, before=before)
    cancel_result = _cancel_async_startup(config, opposite, "switch_engine_async")
    after_cancel = engine_health(config, opposite)
    stop_result = None
    if (pending_intent.get("force_stop_opposite")
            or cancel_result.get("cancelled")
            or before[opposite]["api"]["running"]
            or before[opposite]["backend"]["running"]
            or after_cancel["api"]["running"]
            or after_cancel["backend"]["running"]):
        stop_result = _stop_engine_unlocked(config, opposite)
    latest = _read_json_file(_engine_intent_path()) or {}
    if latest.get("request_token") != request_token:
        log_event("engine_switch_async_superseded_before_start", requested_engine=requested,
                  request_token=request_token, latest_token=latest.get("request_token"))
        return {"ok": True, "cancelled": True, "engine": requested, "requestToken": request_token}
    selected = engine_health(config, requested)
    if selected["running"]:
        _async_start_status(config, requested, selected)
        latest.update({"state": "ready", "ready_at": datetime.now().astimezone().isoformat(timespec="seconds")})
        _write_json_file(_engine_intent_path(), latest)
        log_event("engine_switch_async_already_ready", requested_engine=requested,
                  request_token=request_token)
        final = {name: engine_health(config, name) for name in ENGINE_DEFINITIONS}
        if final[opposite]["api"]["running"] or final[opposite]["backend"]["running"]:
            raise RuntimeError(f"反対側の{ENGINE_DEFINITIONS[opposite]['label']}が停止していません。")
        return {"ok": True, **selected, "currentEngine": requested, "starting": False,
                "restarted": False, "requestToken": request_token, "stopResult": stop_result,
                "engines": final, "message": f"{ENGINE_DEFINITIONS[requested]['label']}は既に稼働中です。"}
    previous = _async_start_status(config, requested, selected)
    if previous.get("state") in {"launching", "running", "launch_complete"}:
        log_event("engine_switch_async_reused", requested_engine=requested,
                  request_token=request_token, startup_pid=previous.get("pid"),
                  startup_state=previous.get("state"))
        return {
            "ok": True, "engine": requested, "label": ENGINE_DEFINITIONS[requested]["label"],
            "running": False, "starting": True, "duplicate": True,
            "startupPid": previous.get("pid"), "startupState": previous.get("state"),
            "startupLog": {"stdout": previous.get("stdout"), "stderr": previous.get("stderr"),
                            "launcherStdout": previous.get("launcherStdout"),
                            "launcherStderr": previous.get("launcherStderr")},
            "endpoint": selected["endpoint"], "stopResult": stop_result,
            "requestToken": request_token,
            "startupTimeoutMs": int(config["startup_timeout_seconds"] * 1000),
            "pollIntervalMs": int(config["startup_poll_interval_seconds"] * 1000),
            "message": f"{ENGINE_DEFINITIONS[requested]['label']}は既に起動処理中です。",
        }
    log_event("engine_switch_async_starting", requested_engine=requested,
              request_token=request_token)
    start_result = _start_engine_async_unlocked(config, requested, request_token=request_token)
    latest = _read_json_file(_engine_intent_path()) or latest
    latest.update({"state": "starting", "startup_pid": start_result.get("startupPid"),
                   "startup_state": start_result.get("startupState") or ("starting" if start_result.get("starting") else "ready")})
    _write_json_file(_engine_intent_path(), latest)
    start_result.update({"switch": True, "requestToken": request_token, "stopResult": stop_result})
    return start_result


def _run_engine_transition_worker(config: dict) -> int:
    """Consume the latest switch intent and cancel superseded starts."""
    log_event("engine_transition_worker_entry", worker_pid=os.getpid(), root=str(config["root"]))
    try:
        initial_intent = _read_json_file(_engine_intent_path()) or {}
        if initial_intent.get("layout") != "unified":
            # A worker from the former two-port implementation must not act
            # on the new shared-entry state machine.
            log_event("legacy_engine_transition_worker_ignored", worker_pid=os.getpid())
            return 0
        for _ in range(8):
            intent = _read_json_file(_engine_intent_path()) or {}
            requested = intent.get("engine")
            request_token = str(intent.get("request_token") or "")
            if intent.get("layout") != "unified" or requested not in ENGINE_DEFINITIONS or not request_token:
                log_event("engine_transition_worker_no_intent", worker_pid=os.getpid())
                return 0
            log_event("engine_transition_worker_lock_wait", worker_pid=os.getpid(),
                      requested_engine=requested, request_token=request_token)
            with _engine_operation_lock():
                log_event("engine_transition_worker_lock_acquired", worker_pid=os.getpid(),
                          requested_engine=requested, request_token=request_token)
                latest = _read_json_file(_engine_intent_path()) or {}
                if latest.get("request_token") != request_token or latest.get("engine") != requested:
                    continue
                try:
                    result = _switch_engine_operation_unlocked(config, requested, request_token)
                    current = _read_json_file(_engine_intent_path()) or {}
                    if current.get("request_token") != request_token:
                        _cancel_async_startup(config, requested, "switch_superseded")
                        _stop_engine_unlocked(config, requested)
                        continue
                    if result.get("cancelled"):
                        continue
                    current.update({"state": "starting" if result.get("starting") else "ready",
                                    "startup_pid": result.get("startupPid"),
                                    "startup_state": result.get("startupState")})
                    _write_json_file(_engine_intent_path(), current)
                    return 0
                except Exception as error:
                    current = _read_json_file(_engine_intent_path()) or {}
                    if current.get("request_token") == request_token:
                        current.update({"state": "failed", "error": str(error),
                                        "failed_at": datetime.now().astimezone().isoformat(timespec="seconds")})
                        _write_json_file(_engine_intent_path(), current)
                        log_event("engine_switch_async_failed", requested_engine=requested,
                                  request_token=request_token, error_type=type(error).__name__, error=str(error))
                        return 1
        log_event("engine_switch_async_superseded_limit")
        return 1
    finally:
        state_path = _engine_worker_state_path()
        state = _read_json_file(state_path) or {}
        # A superseded worker must not mark a newer worker as finished.
        if state.get("pid") in {None, os.getpid()}:
            state.update({"state": "done", "finished_at": datetime.now().astimezone().isoformat(timespec="seconds")})
        try:
            if state.get("pid") in {None, os.getpid()}:
                _write_json_file(state_path, state)
        except OSError:
            pass


def switch_engine_async(config: dict, requested: str, request_token: str = "") -> dict:
    """Select a logical mode; do not restart the shared API or backend."""
    requested = _engine_name(requested)
    request_token = str(request_token or f"native-{os.getpid()}-{time.time_ns()}")
    with _engine_operation_lock():
        _write_selected_engine(requested, request_token)
        common = engine_health(config, "standard")
        if common["running"]:
            _write_json_file(_engine_intent_path(), {
                "layout": "unified", "state": "ready", "engine": requested,
                "request_token": request_token,
                "ready_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            })
            selected = engine_health(config, requested, common["api"], common["backend"])
            log_event("unified_engine_selected", engine=requested, request_token=request_token,
                      endpoint=selected["endpoint"])
            return {"ok": True, **selected, "engine": requested, "currentEngine": requested,
                    "running": True, "starting": False, "restarted": False,
                    "requestToken": request_token, "engineTransition": False,
                    "message": f"{ENGINE_DEFINITIONS[requested]['label']}を選択しました。"}
        return _start_unified_engine_async_unlocked(
            config, requested, request_token=request_token, reason="engine_switch"
        )


def switch_engine(config: dict, requested: str) -> dict:
    """Compatibility alias: all switch requests now use the asynchronous path."""
    return switch_engine_async(config, requested)


def _voices_config(config: dict) -> Path:
    return config["root"] / "config" / "voices.json"


def _read_voices(config: dict) -> dict:
    path = _voices_config(config)
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        value = {"version": 1, "voices": []}
    if not isinstance(value, dict) or not isinstance(value.get("voices", []), list) or not isinstance(value.get("disabled_auto_voices", []), list):
        raise ValueError("voices.jsonの形式が不正です。")
    value.setdefault("version", 1)
    value.setdefault("voices", [])
    value.setdefault("disabled_auto_voices", [])
    return value


def _save_voices(config: dict, value: dict) -> None:
    path = _voices_config(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _connection_refused(error: BaseException) -> bool:
    reason = getattr(error, "reason", error)
    return (
        isinstance(reason, ConnectionRefusedError)
        or getattr(reason, "errno", None) == errno.ECONNREFUSED
        or getattr(reason, "winerror", None) == 10061
    )


def _reload_api(config: dict) -> dict:
    """Report saved-vs-live state; never hide a failed reload of a running API."""
    try:
        parsed = urlsplit(str(config.get("health_url") or ""))
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.username or parsed.password:
            raise ValueError("health_url must refer to a local HTTP API")
        base = f"http://{parsed.netloc}"
        probe = urllib.request.Request(base + "/capabilities", headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(probe, timeout=3) as response:
                capabilities = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"API capability check returned HTTP {error.code}") from error
        except urllib.error.URLError as error:
            if _connection_refused(error):
                return {"status": "stopped", "applied": False}
            raise RuntimeError(f"API status is uncertain: {error}") from error
        if not isinstance(capabilities, dict) or capabilities.get("protocol") != "irodori_voice":
            raise RuntimeError("The service on the configured port is not the Irodori Voice API")

        request = urllib.request.Request(
            base + "/v1/reload",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "Origin": "chrome-extension://" + str(config.get("chrome_extension_id") or "aeaichhflmacmifblidbpojgfemjijlb"),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                body = json.loads(response.read().decode("utf-8"))
                if response.status != 200 or body.get("status") != "reloaded":
                    raise RuntimeError(f"Unexpected reload response (HTTP {response.status})")
                return {"status": "reloaded", "applied": True, "httpStatus": response.status}
        except Exception as error:
            raise RuntimeError(
                f"Voice設定は保存済みですが、起動中APIへの再読み込みに失敗しました。APIを再起動してください。({error})"
            ) from error
    except Exception as error:
        if "Voice設定は保存済み" in str(error):
            raise
        raise RuntimeError(
            f"Voice設定は保存済みですが、API状態を確認できず再読み込みできませんでした。APIを再起動してください。({error})"
        ) from error


def _valid_voice_id(value: object) -> str:
    voice_id = str(value or "").strip()
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z_-]{0,63}", voice_id):
        raise ValueError("Voice IDは半角英数字で始まる64文字以内（英数字・_・-）にしてください。")
    return voice_id


def _managed_voice_files(config: dict, voice_id: str, record: dict) -> list[Path]:
    """Return only exact app-owned files; never infer ownership from a folder."""
    root = Path(config["root"]).resolve()
    app_voice_root = root / "IrodoriVoiceReader"
    managed_root_path = app_voice_root / "voices"
    is_link = lambda path: path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    if is_link(app_voice_root) or is_link(managed_root_path):
        raise ValueError("Voice管理領域にシンボリックリンクがあります。ファイルは削除しません。")
    managed_root = managed_root_path.resolve()
    voice_folder_path = managed_root / voice_id
    if is_link(voice_folder_path):
        raise ValueError("Voiceフォルダーがリンクです。ファイルは削除しません。")
    voice_folder = voice_folder_path.resolve()
    if not voice_folder.is_relative_to(managed_root):
        raise ValueError("Voice管理フォルダーが管理領域の外を指しています。ファイルは削除しません。")
    allowed = {
        "reference_wav": ("reference_wav", "reference.wav"),
        "speaker_embedding": ("speaker_embedding", "speaker.safetensors"),
        "safetensors": ("safetensors", "model.safetensors"),
    }
    files = []
    for field, filename in allowed.values():
        raw = record.get(field)
        if not raw:
            continue
        relative = Path(str(raw))
        expected_relative = Path("IrodoriVoiceReader") / "voices" / voice_id / filename
        target = (root / relative).resolve()
        expected = (managed_root / voice_id / filename).resolve()
        if relative.is_absolute() or relative.as_posix() != expected_relative.as_posix() or target != expected:
            raise ValueError("Voiceファイルの所有パスを検証できません。登録解除のみ行ってください。")
        candidate = root / expected_relative
        if is_link(candidate) or (candidate.exists() and not candidate.is_file()):
            raise ValueError("Voiceファイルが通常ファイルではありません。削除を中止しました。")
        if candidate.is_file():
            files.append(candidate)
    return files


def _valid_auto_voice_id(value: object) -> str:
    voice_id = str(value or "").strip()
    if not re.fullmatch(r"[0-9A-Za-z_\-\u3040-\u30ff\u3400-\u9fff]+", voice_id):
        raise ValueError("自動検出Voice IDが不正です。")
    return voice_id.casefold()


def _disabled_auto_voice_ids(value: dict) -> list[str]:
    return list(dict.fromkeys(
        str(item).strip().casefold()
        for item in value.get("disabled_auto_voices", [])
        if isinstance(item, (str, int)) and str(item).strip()
    ))


def voice_state(config: dict) -> dict:
    value = _read_voices(config)
    output = []
    for item in value["voices"]:
        if not isinstance(item, dict):
            continue
        reference = item.get("speaker_embedding") or item.get("reference_wav") or item.get("safetensors") or ""
        voice_type = "speaker_embedding" if item.get("speaker_embedding") else ("safetensors" if item.get("safetensors") else "reference_wav")
        output.append({
            "id": item.get("id", ""), "name": item.get("name", item.get("id", "")),
            "type": voice_type,
            "reference": reference, "managed": item.get("managed_by") == "irodori-voice-reader",
            "fileExists": bool(reference and (config["root"] / reference).resolve().is_file()),
        })
    return {"ok": True, "voices": output, "disabledAutoVoices": _disabled_auto_voice_ids(value)}


def disable_auto_voice(message: dict, config: dict) -> dict:
    voice_id = _valid_auto_voice_id(message.get("id"))
    value = _read_voices(config)
    disabled = _disabled_auto_voice_ids(value)
    if voice_id not in disabled:
        disabled.append(voice_id)
    value["disabled_auto_voices"] = disabled
    _save_voices(config, value)
    api_reload = _reload_api(config)
    return {"ok": True, "message": "自動検出Voiceを一覧から外しました。元ファイルは削除していません。", "disabledAutoVoices": disabled, "apiReload": api_reload}


def restore_auto_voices(message: dict, config: dict) -> dict:
    value = _read_voices(config)
    disabled = _disabled_auto_voice_ids(value)
    requested = str(message.get("id") or "").strip()
    if requested:
        voice_id = _valid_auto_voice_id(requested)
        disabled = [item for item in disabled if item != voice_id]
    else:
        disabled = []
    value["disabled_auto_voices"] = disabled
    _save_voices(config, value)
    api_reload = _reload_api(config)
    return {"ok": True, "message": "自動検出Voiceを再表示しました。", "disabledAutoVoices": disabled, "apiReload": api_reload}


def import_voice(message: dict, config: dict) -> dict:
    voice_id = _valid_voice_id(message.get("id"))
    name = str(message.get("name") or voice_id).strip()
    kind = str(message.get("voiceType") or "")
    filename = str(message.get("fileName") or "")
    if kind == "reference_wav" and not filename.lower().endswith(".wav"):
        raise ValueError("Reference WAVには.wavファイルを選択してください。")
    if kind == "speaker_embedding" and not filename.lower().endswith(".speaker.safetensors"):
        raise ValueError("Speaker Embeddingには.speaker.safetensorsファイルを選択してください。")
    if kind == "safetensors" and (not filename.lower().endswith(".safetensors") or filename.lower().endswith(".speaker.safetensors")):
        raise ValueError("Safetensorsには通常の.safetensorsファイルを選択してください。")
    if kind not in {"reference_wav", "speaker_embedding", "safetensors"}:
        raise ValueError("未対応のVoice形式です。")
    try:
        payload = base64.b64decode(str(message.get("dataBase64") or ""), validate=True)
    except Exception as error:
        raise ValueError("Voiceファイルを読み取れませんでした。") from error
    if not payload or len(payload) > 64 * 1024 * 1024:
        raise ValueError("Voiceファイルは1バイト以上64MB以下にしてください。")
    managed_root = (config["root"] / "IrodoriVoiceReader" / "voices").resolve()
    target_dir = (managed_root / voice_id).resolve()
    if not target_dir.is_relative_to(managed_root):
        raise ValueError("Voice保存先が不正です。")
    target_dir.mkdir(parents=True, exist_ok=True)
    target_name = {"reference_wav": "reference.wav", "speaker_embedding": "speaker.safetensors", "safetensors": "model.safetensors"}[kind]
    target = target_dir / target_name
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, target)
    relative = target.relative_to(config["root"]).as_posix()
    value = _read_voices(config)
    existing = next((item for item in value["voices"] if isinstance(item, dict) and item.get("id") == voice_id), None)
    record = {"id": voice_id, "name": name, "managed_by": "irodori-voice-reader"}
    field = {"reference_wav": "reference_wav", "speaker_embedding": "speaker_embedding", "safetensors": "safetensors"}[kind]
    record[field] = relative
    if existing is None:
        value["voices"].append(record)
    else:
        existing.clear()
        existing.update(record)
    _save_voices(config, value)
    api_reload = _reload_api(config)
    return {"ok": True, "message": f"Voice「{name} / {voice_id}」を登録しました。", "voice": record, "apiReload": api_reload}


def delete_voice(message: dict, config: dict) -> dict:
    if message.get("restoreAuto"):
        return restore_auto_voices(message, config)
    if message.get("source") == "auto" or message.get("disableAuto"):
        return disable_auto_voice(message, config)
    voice_id = _valid_voice_id(message.get("id"))
    value = _read_voices(config)
    record = next((item for item in value["voices"] if isinstance(item, dict) and item.get("id") == voice_id), None)
    if record is None:
        raise ValueError("削除するVoice登録が見つかりません。")
    if bool(message.get("deleteManagedFile")) and record.get("managed_by") != "irodori-voice-reader":
        raise ValueError("元から存在するファイルは削除できません。Voice登録だけ削除してください。")
    owned_files = _managed_voice_files(config, voice_id, record) if bool(message.get("deleteManagedFile")) else []
    value["voices"].remove(record)
    _save_voices(config, value)
    deleted_files = []
    retained_files = []
    for path in owned_files:
        try:
            path.unlink()
            deleted_files.append(path.name)
        except OSError:
            retained_files.append(path.name)
    api_reload = _reload_api(config)
    message_text = "Voice登録を削除しました。"
    if retained_files:
        message_text += " 管理対象ファイルの一部は削除できず、登録解除のみ完了しました。"
    return {
        "ok": True,
        "message": message_text,
        "deletedManagedFile": bool(deleted_files),
        "deletedManagedFiles": deleted_files,
        "retainedManagedFiles": retained_files,
        "apiReload": api_reload,
    }


def update_voice(message: dict, config: dict) -> dict:
    voice_id = _valid_voice_id(message.get("id"))
    name = str(message.get("name") or voice_id).strip()
    value = _read_voices(config)
    record = next((item for item in value["voices"] if isinstance(item, dict) and item.get("id") == voice_id), None)
    if record is None:
        raise ValueError("変更するVoice登録が見つかりません。")
    record["name"] = name
    _save_voices(config, value)
    api_reload = _reload_api(config)
    return {"ok": True, "message": f"Voice「{name} / {voice_id}」を変更しました。", "apiReload": api_reload}


def _dataset_base_dir(message: dict) -> Path:
    raw = str(message.get("base_dir") or "").strip()
    if not raw:
        raise ValueError("WAV保存先フォルダーを指定してください。")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("WAV保存先には絶対パスを指定してください。")
    resolved = path.resolve()
    if not resolved.is_dir():
        raise ValueError("WAV保存先フォルダーが存在しません。")
    return resolved


def validate_dataset_directory(message: dict) -> dict:
    try:
        path = _dataset_base_dir(message)
    except (OSError, ValueError) as error:
        return {"ok": True, "valid": False, "error": str(error)}
    return {"ok": True, "valid": True, "path": str(path)}


def _decode_process_output(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932", "utf-16"):
        try:
            value = bytes(raw or b"").decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\ufffd" not in value:
            return value
    return bytes(raw or b"").decode("utf-8", errors="replace")


def choose_dataset_directory() -> dict:
    """Open the native Windows folder picker and return only a chosen path."""
    if os.name != "nt":
        raise RuntimeError("Windowsのフォルダー選択はこの環境では利用できません。")
    command = (
        "$OutputEncoding = New-Object System.Text.UTF8Encoding($false); "
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$dialog.Description = 'Choose WAV dataset folder'; "
        "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
        "{ [Console]::Out.WriteLine($dialog.SelectedPath) }"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-STA",
             "-ExecutionPolicy", "Bypass", "-Command", command],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=300, creationflags=CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB,
        )
    except OSError as error:
        # Older Windows configurations can reject breakaway for an interactive
        # helper; retrying without that flag does not change the selected path.
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-STA",
             "-ExecutionPolicy", "Bypass", "-Command", command],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=300, creationflags=CREATE_NO_WINDOW,
        )
    if result.returncode != 0:
        detail = _decode_process_output(result.stderr).strip()
        raise RuntimeError(f"フォルダー選択ダイアログを開けませんでした。{detail}".strip())
    selected = next((line.strip() for line in reversed(_decode_process_output(result.stdout).splitlines()) if line.strip()), "")
    if not selected:
        return {"ok": True, "cancelled": True}
    checked = validate_dataset_directory({"base_dir": selected})
    if not checked.get("valid"):
        return {"ok": True, "cancelled": False, "valid": False, "error": checked.get("error")}
    log_event("dataset_directory_chosen")
    return {"ok": True, "cancelled": False, "valid": True, "path": checked["path"]}


def _dataset_component(value: object, fallback: str) -> str:
    """Make a user-provided label safe for one Windows directory component."""
    text = str(value or "").strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\.{2,}", "_", text).strip(" .")
    if not text or text in {".", ".."}:
        text = fallback
    return text[:120]


def _dataset_request_id(value: object) -> str:
    requested = str(value or "").strip()
    if requested and re.fullmatch(r"[0-9A-Za-z_-]{8,128}", requested):
        return requested
    return uuid.uuid4().hex


@contextmanager
def _dataset_capture_lock():
    """Serialize metadata read/modify/write across Native Host invocations."""
    path = _native_log_dir() / "dataset-capture.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0)
    handle.write(b"0")
    handle.flush()
    locked = False
    deadline = time.monotonic() + ENGINE_OPERATION_LOCK_TIMEOUT_SECONDS
    try:
        if os.name == "nt":
            import msvcrt

            while time.monotonic() < deadline:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl

            while time.monotonic() < deadline:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError:
                    time.sleep(0.05)
        if not locked:
            raise TimeoutError("WAV保存処理が終了していません。")
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _dataset_wav_bytes(item: dict) -> bytes:
    encoded = item.get("wav_base64") or item.get("dataBase64") or item.get("base64")
    try:
        payload = base64.b64decode(str(encoded or ""), validate=True)
    except Exception as error:
        raise ValueError("WAVデータを読み取れませんでした。") from error
    if len(payload) < 44 or len(payload) > MAX_DATASET_WAV_BYTES:
        raise ValueError("保存対象WAVのサイズが不正です。")
    if payload[:4] != b"RIFF" or payload[8:12] != b"WAVE":
        raise ValueError("保存対象が正常なWAVではありません。")
    return payload


def _dataset_text(value: object, field: str) -> str:
    text = str(value or "")
    if len(text.encode("utf-8")) > MAX_DATASET_TEXT_BYTES:
        raise ValueError(f"{field}が大きすぎます。")
    return text


def _dataset_segments(message: dict) -> list[dict]:
    values = message.get("segments")
    if not isinstance(values, list):
        values = message.get("wav_files")
    if not isinstance(values, list):
        single = message.get("segment")
        values = [single] if isinstance(single, dict) else []
    if not values:
        raise ValueError("保存するWAVがありません。")
    output = []
    for position, item in enumerate(values, 1):
        if not isinstance(item, dict):
            raise ValueError("WAVセグメントの形式が不正です。")
        try:
            index = int(item.get("index", position))
        except (TypeError, ValueError) as error:
            raise ValueError("WAVセグメント番号が不正です。") from error
        if index < 1 or index > 999999:
            raise ValueError("WAVセグメント番号が不正です。")
        output.append({
            "index": index,
            "text": _dataset_text(item.get("text"), "セグメント本文"),
            "payload": _dataset_wav_bytes(item),
        })
    return output


def save_tts_dataset(message: dict) -> dict:
    """Save already-generated WAV bytes and their exact TTS text.

    The extension sends one or more completed chunks for one request ID.  The
    host owns all filesystem access, sanitizes the character directory name,
    and atomically updates metadata.json so playback never depends on saving.
    """
    base_dir = _dataset_base_dir(message)
    character_raw = str(message.get("character") or "").strip()
    character = _dataset_component(character_raw, "_Untagged_")
    request_id = _dataset_request_id(message.get("request_id"))
    request_dir = (base_dir / character / request_id).resolve()
    character_root = (base_dir / character).resolve()
    if not request_dir.is_relative_to(character_root):
        raise ValueError("WAV保存先が不正です。")
    segments = _dataset_segments(message)
    metadata_input = message.get("metadata") if isinstance(message.get("metadata"), dict) else message
    metadata_path = request_dir / "metadata.json"
    with _dataset_capture_lock():
        request_dir.mkdir(parents=True, exist_ok=True)
        try:
            existing = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("既存metadata.jsonを読み取れませんでした。") from error
        if not isinstance(existing, dict):
            existing = {}
        metadata = {
            "character": _dataset_text(metadata_input.get("character") or character_raw or character, "character"),
            "tag": _dataset_text(metadata_input.get("tag"), "tag"),
            "voice_id": _dataset_text(metadata_input.get("voice_id"), "voice_id"),
            "voice_name": _dataset_text(metadata_input.get("voice_name"), "voice_name"),
            "engine": _dataset_text(metadata_input.get("engine"), "engine"),
            "source": _dataset_text(metadata_input.get("source"), "source"),
            "created_at": _dataset_text(metadata_input.get("created_at") or datetime.now().astimezone().isoformat(timespec="seconds"), "created_at"),
            "original_text": _dataset_text(metadata_input.get("original_text"), "original_text"),
            "segments": list(existing.get("segments", [])) if isinstance(existing.get("segments"), list) else [],
        }
        by_index = {int(item.get("index")): item for item in metadata["segments"] if isinstance(item, dict) and str(item.get("index", "")).isdigit()}
        written = []
        for segment in segments:
            index = segment["index"]
            wav_name = f"{index:03d}.wav"
            wav_path = request_dir / wav_name
            text = segment["text"]
            previous = by_index.get(index)
            if previous and (previous.get("text") != text or previous.get("wav") != wav_name):
                raise ValueError(f"WAVセグメント {index} は既に別の内容で保存されています。")
            if wav_path.exists():
                if previous:
                    continue
                raise ValueError(f"WAVセグメント {index} は既に存在します。")
            temporary = wav_path.with_suffix(wav_path.suffix + ".tmp")
            temporary.write_bytes(segment["payload"])
            os.replace(temporary, wav_path)
            entry = {"index": index, "wav": wav_name, "text": text}
            by_index[index] = entry
            written.append(wav_name)
        metadata["segments"] = [by_index[index] for index in sorted(by_index)]
        temporary_metadata = metadata_path.with_suffix(".json.tmp")
        temporary_metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary_metadata, metadata_path)
    log_event("dataset_saved", request_id=request_id, character=character, segment_count=len(written), directory=str(request_dir))
    return {"ok": True, "requestDir": str(request_dir), "metadata": str(metadata_path),
            "written": written, "segmentCount": len(metadata["segments"])}


def _recover_stale_engine_transition(config: dict, intent: dict | None) -> dict | None:
    """Recover one detached worker that died with the Native Host.

    Firefox polls status while a switch is in progress.  If the worker was
    killed by the host's Job object, leaving ``switching`` forever would make
    the sidebar wait until its long timeout.  Restart once (the worker itself
    now uses CREATE_BREAKAWAY_FROM_JOB); a second unexpected exit is surfaced
    as a concrete failure instead of being retried forever.
    """
    if not intent or intent.get("state") not in {"switching", "starting"}:
        return _read_json_file(_engine_worker_state_path())
    worker = _read_json_file(_engine_worker_state_path()) or {}
    if str(worker.get("state") or "") not in {"running", "queued"}:
        return worker
    alive, exit_code = _process_status(worker.get("pid"))
    if alive:
        return worker
    try:
        restart_count = int(worker.get("restart_count", 0))
    except (TypeError, ValueError):
        restart_count = 0
    log_event("engine_transition_worker_stale", worker_pid=worker.get("pid"),
              exit_code=exit_code, restart_count=restart_count,
              requested_engine=intent.get("engine"), request_token=intent.get("request_token"))
    if restart_count < 1:
        worker.update({"state": "stale", "exit_code": exit_code,
                       "stale_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                       "restart_count": restart_count + 1})
        try:
            _write_json_file(_engine_worker_state_path(), worker)
            replacement = _spawn_engine_transition_worker(config, restart_count=restart_count + 1)
            log_event("engine_transition_worker_restarted", previous_pid=worker.get("pid"),
                      worker_pid=replacement.get("pid"), restart_count=restart_count + 1,
                      requested_engine=intent.get("engine"), request_token=intent.get("request_token"))
            return replacement
        except Exception as error:
            log_event("engine_transition_worker_restart_error", error_type=type(error).__name__,
                      error=str(error), requested_engine=intent.get("engine"),
                      request_token=intent.get("request_token"))
            intent.update({"state": "failed", "error": f"engine切替workerを再起動できませんでした: {error}",
                           "failed_at": datetime.now().astimezone().isoformat(timespec="seconds")})
            try:
                _write_json_file(_engine_intent_path(), intent)
            except OSError:
                pass
            return worker
    intent.update({"state": "failed",
                   "error": "engine切替workerが予期せず終了しました。もう一度切り替えてください。",
                   "failed_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    try:
        _write_json_file(_engine_intent_path(), intent)
    except OSError:
        pass
    log_event("engine_transition_worker_failed_after_restart", worker_pid=worker.get("pid"),
              exit_code=exit_code, requested_engine=intent.get("engine"),
              request_token=intent.get("request_token"))
    return worker


def handle(message: dict, config: dict) -> dict:
    command = message.get("command")
    if command == "status":
        requested = _engine_name(message.get("engine") or "standard")
        shared_api = _http_health(config["health_url"])
        shared_backend = _http_health(config["backend_health_url"])
        shared_running = shared_api["running"] and shared_backend["running"]
        engines = {name: engine_health(config, name, shared_api, shared_backend) for name in ENGINE_DEFINITIONS}
        selected = _read_selected_engine()
        intent = _read_json_file(_engine_intent_path()) or {}
        # Intents written by the old two-port coordinator are stale after the
        # migration.  Only the explicit unified marker can affect readiness.
        unified_intent = intent if intent.get("layout") == "unified" else {}
        startup = _async_start_status(config, "standard", {"running": shared_running})
        transition_active = bool(
            not shared_running
            and unified_intent.get("engine") == selected
            and unified_intent.get("state") in {"switching", "starting"}
        )
        if shared_running and unified_intent.get("state") in {"switching", "starting"}:
            unified_intent.update({"state": "ready", "ready_at": datetime.now().astimezone().isoformat(timespec="seconds")})
            try:
                _write_json_file(_engine_intent_path(), unified_intent)
            except OSError:
                pass
            transition_active = False
        intent_error = None
        if unified_intent.get("engine") == requested and unified_intent.get("state") == "failed":
            intent_error = unified_intent.get("error") or "Irodori APIの起動に失敗しました。"
        current = selected if shared_running else "none"
        worker = _read_json_file(_engine_worker_state_path()) or {}
        worker_alive, worker_exit_code = _process_status(worker.get("pid")) if worker else (False, None)
        requested_engine = unified_intent.get("engine") if transition_active else None
        return {"ok": True, "helperVersion": HOST_VERSION, "running": shared_running,
                "backendRunning": shared_backend["running"], "currentEngine": current,
                "startupLog": str(Path(__file__).with_name("native_host.log")),
                "startupState": startup.get("state"), "startupPid": startup.get("pid"),
                "startupError": startup.get("error") if transition_active else intent_error,
                "startupExitCode": startup.get("exitCode"),
                "startupOutput": {"stdout": startup.get("stdout"), "stderr": startup.get("stderr"),
                                  "launcherStdout": startup.get("launcherStdout"), "launcherStderr": startup.get("launcherStderr")},
                "engineTransition": transition_active, "requestedEngine": requested_engine,
                "requestToken": unified_intent.get("request_token") if unified_intent else None,
                "transitionWorker": {"pid": worker.get("pid"), "state": worker.get("state"),
                                      "alive": worker_alive, "exitCode": worker_exit_code},
                "sharedApi": True, "endpoint": config["health_url"].rsplit("/", 1)[0],
                "engines": engines}
    if command in {"switch_engine", "switch_engine_async"}:
        return switch_engine_async(config, message.get("engine"), message.get("requestToken") or message.get("requestId") or "")
    if command in {"start_standard", "start_stable"}:
        return start_engine(config, "standard" if command == "start_standard" else "voice_stable")
    if command == "start_engine_async":
        return start_engine_async(config, message.get("engine"),
                                  request_token=message.get("requestToken") or "",
                                  reason=str(message.get("reason") or ""))
    if command in {"stop_standard", "stop_stable"}:
        return stop_engine(config, "standard" if command == "stop_standard" else "voice_stable")
    if command == "start":
        if message.get("engine"):
            return start_engine(config, message.get("engine"))
        state = health(config)
        backend = backend_health(config)
        log_event("start_request", root=str(config["root"]), task=config["start_task"],
                  backend_health=backend["running"], api_health=state["running"])
        if state["running"]:
            log_event("start_already_ready", root=str(config["root"]))
            return {"ok": True, "running": True, "backendRunning": backend["running"], "message": "Irodori APIは既に稼働中です。"}
        if config["start_task"]:
            result = subprocess.run(
                ["schtasks.exe", "/Run", "/TN", config["start_task"]],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
            log_event("task_launch", root=str(config["root"]), task=config["start_task"], returncode=result.returncode)
            if result.returncode != 0:
                raise RuntimeError("API start task could not be launched.")
        else:
            launch = _launch_script_async(config, "start_kurisu_voice_api.ps1")
            log_event("script_launch", root=str(config["root"]), script="start_kurisu_voice_api.ps1",
                      pid=launch.get("pid"), stdout=launch.get("stdout"), stderr=launch.get("stderr"))
        _spawn_startup_monitor()
        return {"ok": True, "running": False, "starting": True, "backendRunning": backend["running"],
                "startupTimeoutMs": int(config["startup_timeout_seconds"] * 1000),
                "pollIntervalMs": int(config["startup_poll_interval_seconds"] * 1000),
                "message": "Irodori APIの起動要求を受け付けました。状態確認を続けます。"}
    if command == "stop":
        if message.get("engine"):
            return stop_engine(config, message.get("engine"))
        run_script(config, "stop_kurisu_voice_api.ps1", 25)
        state = wait_for_state(config, False, 8)
        if state["running"]:
            raise RuntimeError("API停止後もポートが応答しています。")
        return {"ok": True, "running": False, "message": "Irodori APIを停止しました。"}
    if command == "voice_state":
        return voice_state(config)
    if command == "voice_import":
        return import_voice(message, config)
    if command == "voice_delete":
        return delete_voice(message, config)
    if command == "voice_update":
        return update_voice(message, config)
    if command == "validate_dataset_directory":
        return validate_dataset_directory(message)
    if command == "choose_dataset_directory":
        return choose_dataset_directory()
    if command == "save_tts_dataset":
        return save_tts_dataset(message)
    if command == "timing_log_append":
        return timing_log_append(message)
    if command == "open_timing_log_folder":
        return open_timing_log_folder()
    raise ValueError("許可されていない操作です。")


def verify_invoker() -> None:
    if len(sys.argv) >= 3 and sys.argv[2] and sys.argv[2] != str(load_config().get("firefox_extension_id") or EXPECTED_EXTENSION_ID):
        raise PermissionError("許可されていない拡張機能です。")


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--monitor-startup":
        try:
            return monitor_startup(load_config())
        except Exception as error:
            log_event("startup_monitor_error", error=str(error))
            return 1
    if len(sys.argv) >= 2 and sys.argv[1] == "--engine-transition-worker":
        try:
            return _run_engine_transition_worker(load_config())
        except Exception as error:
            log_event("engine_transition_worker_error", error_type=type(error).__name__, error=str(error))
            return 1
    try:
        verify_invoker()
        config = load_config()
        message = read_message()
        if message is None:
            return 0
        command = str(message.get("command") or "")
        log_event("request", command=command, root=str(config["root"]))
        try:
            write_message(handle(message, config))
        except Exception as error:  # response must stay valid even when a controlled operation fails
            log_event("request_error", command=command, error_type=type(error).__name__, error=str(error), root=str(config["root"]))
            write_message({"ok": False, "error": str(error), "helperVersion": HOST_VERSION})
        return 0
    except Exception as error:
        log_event("host_error", error_type=type(error).__name__, error=str(error))
        try:
            write_message({"ok": False, "error": str(error), "helperVersion": HOST_VERSION})
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
