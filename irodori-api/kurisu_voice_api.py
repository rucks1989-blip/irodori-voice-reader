import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import requests
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from kurisu_voice import KurisuVoice
from kurisu_voice.backends import AudioCppBackend
from kurisu_voice.profiles import load_profiles, profile_from_config
from kurisu_voice.speakers import clear_speaker_cache
from kurisu_voice.stable_trace import collect_trace, stable_trace_path, trace_offset
from kurisu_voice.voices import VoiceRegistry

# Redirected Windows logs can inherit a non-Unicode encoding. Diagnostics must
# never turn a successful synthesis into HTTP 400 through UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf8", errors="backslashreplace")


ROOT = Path(__file__).resolve().parent


def runtime_setting(name, default):
    # An installed environment must never inherit another installation's paths,
    # ports, voice storage or backend URL through global IRODORI_* variables.
    isolated = (ROOT.parent / "chrome-identity.json").is_file() and (ROOT.parent / ".ivr-install.json").is_file()
    return default if isolated else os.environ.get(name, default)

CONFIG_PATH = Path(runtime_setting("IRODORI_VOICE_CONFIG", str(ROOT / "config" / "config.json")))
STABLE_CONFIG_PATH = Path(runtime_setting("IRODORI_STABLE_VOICE_CONFIG", str(ROOT / "config" / "config.voice_stable.json")))
CONFIG_EXAMPLE_PATH = ROOT / "config" / "config.example.json"
PROFILES_PATH = ROOT / "config" / "chunk_profiles.json"
API_CONFIG_PATH = Path(runtime_setting("IRODORI_API_CONFIG", str(ROOT / "config" / "api.json")))
VOICES_CONFIG_PATH = Path(runtime_setting("IRODORI_VOICES_CONFIG", str(ROOT / "config" / "voices.json")))
PID_PATH = Path(runtime_setting("IRODORI_API_PID", str(ROOT / "logs" / "kurisu_voice_api" / "server.pid")))
SYNTHESIS_LOCK = threading.Lock()
API_STOPPING = threading.Event()
STATE_LOCK = threading.RLock()
ENGINE_ALIASES = {
    "normal": "standard",
    "default": "standard",
    "standard": "standard",
    "stable": "voice_stable",
    "safe": "voice_stable",
    "voice_stable": "voice_stable",
    "voice-stable": "voice_stable",
}
ENGINE_LABELS = {
    "standard": "通常版",
    "voice_stable": "声質安定版",
}
TRUSTED_CHROME_EXTENSION_ID = "aeaichhflmacmifblidbpojgfemjijlb"
_identity_path = ROOT.parent / "chrome-identity.json"
if (ROOT.parent / ".ivr-install.json").is_file() and _identity_path.is_file():
    _identity = json.loads(_identity_path.read_text(encoding="utf8"))
    _extension_id = str(_identity.get("extensionId", ""))
    if not re.fullmatch(r"[a-p]{32}", _extension_id):
        raise ValueError("独立環境のChrome識別情報が不正です。")
    TRUSTED_CHROME_EXTENSION_ID = _extension_id
TRUSTED_CHROME_ORIGIN = f"chrome-extension://{TRUSTED_CHROME_EXTENSION_ID}"


def trusted_extension_origin(origin):
    value = str(origin or "").strip()
    if value == TRUSTED_CHROME_ORIGIN:
        return True
    # Firefox assigns a per-profile UUID as a moz-extension origin. The UUID
    # has no path/port and cannot be forged by an ordinary web page.
    return bool(re.fullmatch(r"moz-extension://[0-9a-f-]{16,64}", value, re.IGNORECASE))


def _read_json(path, fallback=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return {} if fallback is None else fallback


def _resolve(value):
    path = Path(str(value or "")).expanduser()
    return str((ROOT / path).resolve()) if value and not path.is_absolute() else str(path)


def normalize_engine(value="standard"):
    key = str(value or "standard").strip().lower().replace(" ", "_")
    try:
        return ENGINE_ALIASES[key]
    except KeyError as exc:
        raise ValueError("未対応の生成エンジンです。") from exc


class State:
    def __init__(self):
        self.reload()

    def reload(self):
        with STATE_LOCK:
            example = _read_json(CONFIG_EXAMPLE_PATH)
            standard = dict(example, **_read_json(CONFIG_PATH))
            stable = dict(example, **_read_json(STABLE_CONFIG_PATH))
            self.engine_configs = {
                "standard": standard,
                "voice_stable": stable,
            }
            # Keep the historical attribute for callers that only inspect the
            # default/normal configuration.  Requests select the mode below.
            self.config = standard
            self.profiles = load_profiles(PROFILES_PATH)
            self.api = {
                "host": "127.0.0.1",
                "port": int(runtime_setting("IRODORI_API_PORT", "8765")),
                "max_input_chars": 20000,
            }
            self.api.update(_read_json(API_CONFIG_PATH))
            # Local-only is a design invariant, not a user-overridable convenience.
            self.api["host"] = "127.0.0.1"
            self.api["port"] = int(runtime_setting("IRODORI_API_PORT", str(self.api["port"])))
            clear_speaker_cache()
            self.voices = VoiceRegistry(ROOT, VOICES_CONFIG_PATH)
            self.device = self._device_name()

    @staticmethod
    def _device_name():
        runtime = _read_json(ROOT / "config" / "runtime.json")
        server = Path(runtime.get("audio_cpp_shared_config") or "config/audio_cpp_server.shared.json")
        backend = _read_json(server if server.is_absolute() else ROOT / server).get("backend")
        if backend == "cpu":
            return "CPU"
        if backend != "cuda":
            return "実行方式未確認"
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=3, check=True,
            )
            return result.stdout.splitlines()[0].strip() or "GPU名を確認できません"
        except Exception:
            return "GPU名を確認できません"

    def profile(self, name=None, engine_name="standard"):
        engine_name = normalize_engine(engine_name)
        config = self.engine_configs[engine_name]
        selected = str(name or config.get("chunk_profile_name") or "標準250文字")
        if selected not in self.profiles:
            raise KeyError(f"分割プロファイルが見つかりません: {selected}")
        profile = dict(self.profiles[selected])
        # The currently selected profile settings saved by the UI take precedence.
        if selected == str(config.get("chunk_profile_name") or ""):
            profile.update(profile_from_config(config))
        profile["speaker_voice_dir"] = _resolve(profile.get("speaker_voice_dir") or "speaker_voice_refs")
        default_voice = config.get("irodori_audiocpp_voice_ref_path", "")
        profile["default_voice_path"] = _resolve(default_voice) if default_voice else ""
        return selected, profile

    def engine(self, profile_name=None, voice_id=None, speaker_tag_policy="profile", engine_name="standard"):
        engine_name = normalize_engine(engine_name)
        config = self.engine_configs[engine_name]
        selected, profile = self.profile(profile_name, engine_name)
        if speaker_tag_policy == "preserve":
            # Firefox already applied its registered-only character mapping.
            # Keep every other 【...】 literal for this request only.
            profile["speaker_switch_enabled"] = False
        profile["speaker_tag_policy"] = speaker_tag_policy
        if voice_id:
            voice = self.voices.get(voice_id)
            if voice["type"] == "safetensors":
                raise ValueError("このVoice形式は現在のaudio.cppバックエンドでは直接利用できません。")
            profile["default_voice_path"] = voice["path"] if voice["type"] == "reference_wav" else ""
            profile["default_speaker_embedding_path"] = voice["path"] if voice["type"] == "speaker_embedding" else ""
        backend = AudioCppBackend(
            runtime_setting("IRODORI_AUDIO_CPP_API_URL", config.get("irodori_audiocpp_api_url", "http://127.0.0.1:8091/v1/audio/speech")),
            config.get("irodori_audiocpp_model", "irodori-500m-v3-q8"),
            config.get("irodori_audiocpp_num_steps", 40),
            config.get("irodori_audiocpp_seed", 1234),
            request_options=config.get("irodori_audiocpp_request_options", {}),
        )
        return selected, KurisuVoice(ROOT, profile, backend)

    def model(self, engine_name="standard"):
        engine_name = normalize_engine(engine_name)
        return self.engine_configs[engine_name].get("irodori_audiocpp_model", "irodori-500m-v3-q8")


STATE = State()


def api_capabilities():
    return {"protocol": "irodori_voice", "shutdown_path": "/shutdown", "voice_registration": True,
            "voice_paths": {"state": "/voices/state", "import": "/voices/import", "update": "/voices/update", "delete": "/voices/delete"}}


def stop_api(handler):
    """Stop the existing API and its existing backend, not a model-only service."""
    if API_STOPPING.is_set():
        handler._send_json(409, {"error": "API shutdown is already in progress."})
        return
    API_STOPPING.set()
    try:
        with SYNTHESIS_LOCK:
            bases = {runtime_setting("IRODORI_AUDIO_CPP_API_URL", config.get("irodori_audiocpp_api_url", "http://127.0.0.1:8091/v1/audio/speech")).split("/v1/", 1)[0].rstrip("/") for config in STATE.engine_configs.values()}
            for base in bases:
                try:
                    response = requests.post(base + "/shutdown", json={}, timeout=15)
                    response.raise_for_status()
                except requests.exceptions.Timeout:
                    raise
                except requests.exceptions.ConnectionError:
                    try:
                        requests.get(base + "/health", timeout=1)
                    except requests.exceptions.Timeout:
                        raise
                    except requests.exceptions.ConnectionError:
                        continue
                    raise
            deadline = time.monotonic() + 600
            for base in bases:
                while True:
                    try:
                        requests.get(base + "/health", timeout=1)
                    except requests.exceptions.Timeout:
                        pass
                    except requests.exceptions.ConnectionError:
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError("The audio API has not finished shutting down.")
                    time.sleep(0.15)
    except Exception:
        API_STOPPING.clear()
        raise
    handler._send_json(200, {"status": "stopping"})
    threading.Thread(target=handler.server.shutdown, daemon=True).start()


def _voice_config():
    try:
        value = json.loads(VOICES_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        value = {"version": 1, "voices": []}
    if not isinstance(value, dict) or not isinstance(value.get("voices", []), list) or not isinstance(value.get("disabled_auto_voices", []), list):
        raise ValueError("voices.jsonの形式が正しくありません。")
    value.setdefault("version", 1)
    value.setdefault("voices", [])
    value.setdefault("disabled_auto_voices", [])
    return value


def _save_voice_config(value):
    VOICES_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = VOICES_CONFIG_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, VOICES_CONFIG_PATH)
    STATE.reload()


def _voice_id(value, auto=False):
    text = str(value or "").strip()
    pattern = r"[0-9A-Za-z_\-\u3040-\u30ff\u3400-\u9fff]+" if auto else r"[0-9A-Za-z][0-9A-Za-z_-]{0,63}"
    if not re.fullmatch(pattern, text):
        raise ValueError("Voice IDの形式が正しくありません。英数字で始まる64文字以内の英数字・_・-を指定してください。")
    return text.casefold()


def voice_state():
    with STATE_LOCK:
        value = _voice_config()
        items = []
        for item in value["voices"]:
            if not isinstance(item, dict):
                continue
            reference = item.get("speaker_embedding") or item.get("reference_wav") or item.get("safetensors") or ""
            kind = "speaker_embedding" if item.get("speaker_embedding") else ("safetensors" if item.get("safetensors") else "reference_wav")
            items.append({"id": item.get("id", ""), "name": item.get("name", item.get("id", "")), "type": kind,
                          "reference": reference, "managed": item.get("managed_by") == "irodori-voice-reader", "fileExists": bool(reference and (ROOT / reference).resolve().is_file())})
        return {"ok": True, "voices": items, "disabledAutoVoices": value["disabled_auto_voices"]}


def voice_change(action, message):
    """The former Voice-management operations, now inside this HTTP API."""
    with STATE_LOCK:
        value = _voice_config()
        if action == "delete" and message.get("restoreAuto"):
            value["disabled_auto_voices"] = []
            _save_voice_config(value)
            return {"ok": True}
        if action == "delete" and (message.get("source") == "auto" or message.get("disableAuto")):
            voice_id = _voice_id(message.get("id"), auto=True)
            value["disabled_auto_voices"] = list(dict.fromkeys([*value["disabled_auto_voices"], voice_id]))
            _save_voice_config(value)
            return {"ok": True, "deletedManagedFile": False}
        voice_id = _voice_id(message.get("id"))
        existing = next((item for item in value["voices"] if isinstance(item, dict) and str(item.get("id", "")).casefold() == voice_id), None)
        name = str(message.get("name") or voice_id).strip()
        if not name or len(name) > 256:
            raise ValueError("表示名は1文字以上256文字以下にしてください。")
        managed_root = Path(runtime_setting("IRODORI_VOICE_STORAGE", str(ROOT / "IrodoriVoiceReader/voices"))).resolve()
        folder = (managed_root / voice_id).resolve()
        if not folder.is_relative_to(managed_root):
            raise ValueError("Voice保存先が正しくありません。")
        if action == "import":
            kind = message.get("voiceType")
            filename = str(message.get("fileName") or "").lower()
            if kind not in {"reference_wav", "speaker_embedding", "safetensors"}:
                raise ValueError("未対応のVoice形式です。")
            is_speaker = filename == "speaker.safetensors" or filename.endswith(".speaker.safetensors")
            if (kind == "reference_wav" and not filename.endswith(".wav")) or (kind == "speaker_embedding" and not is_speaker) or (kind == "safetensors" and (not filename.endswith(".safetensors") or is_speaker)):
                raise ValueError("選択ファイルとVoice形式が一致しません。")
            payload = base64.b64decode(str(message.get("dataBase64") or ""), validate=True)
            if not payload or len(payload) > 64 * 1024 * 1024:
                raise ValueError("音源ファイルは1バイト以上64MB以下にしてください。")
            if kind == "reference_wav" and (len(payload) < 44 or payload[:4] != b"RIFF" or payload[8:12] != b"WAVE"):
                raise ValueError("正常なWAVファイルを選択してください。")
            folder.mkdir(parents=True, exist_ok=True)
            file = folder / {"reference_wav": "reference.wav", "speaker_embedding": "speaker.safetensors", "safetensors": "model.safetensors"}[kind]
            temporary = file.with_suffix(file.suffix + ".tmp")
            temporary.write_bytes(payload)
            os.replace(temporary, file)
            reference = file.relative_to(ROOT).as_posix() if file.is_relative_to(ROOT) else str(file)
            record = {"id": voice_id, "name": name, "managed_by": "irodori-voice-reader", kind: reference}
            if existing is None:
                value["voices"].append(record)
            else:
                existing.clear()
                existing.update(record)
            _save_voice_config(value)
            return {"ok": True, "voice": record}
        if existing is None:
            raise ValueError("Voice登録が見つかりません。")
        if action == "update":
            existing["name"] = name
            _save_voice_config(value)
            return {"ok": True}
        if action == "delete":
            delete_file = bool(message.get("deleteManagedFile"))
            if delete_file and existing.get("managed_by") != "irodori-voice-reader":
                raise ValueError("既存の元ファイルは削除できません。登録だけを削除してください。")
            value["voices"].remove(existing)
            _save_voice_config(value)
            if delete_file:
                for filename in ("reference.wav", "speaker.safetensors", "model.safetensors"):
                    (folder / filename).unlink(missing_ok=True)
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
            return {"ok": True, "deletedManagedFile": delete_file}
        raise ValueError("未対応のVoice操作です。")


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _log_planner_trace(path, profile_name, speaker_tag_policy, trace):
    """Emit a UTF-8 diagnostic record without changing planner output."""
    record = {
        "event": "planner_trace",
        "path": path,
        "profile": profile_name,
        "speaker_tag_policy": speaker_tag_policy,
        "input": trace.get("input", ""),
        "planner_internal": {
            **(trace.get("internal") or {}),
            "normalized_text": trace.get("normalized_text", ""),
            "filtered_text": trace.get("filtered_text", ""),
            "speaker_segments": trace.get("speaker_segments", []),
        },
        "output": {
            "chunk_text": [str(item.get("text", "")) for item in trace.get("chunks", [])],
            "chunks": trace.get("chunks", []),
        },
    }
    print("[planner] " + json.dumps(record, ensure_ascii=False, separators=(",", ":")), flush=True)


def _safe_timing_context(payload):
    """Keep only identifiers needed to correlate API timing with Firefox."""
    raw = payload.get("timing_context")
    if not isinstance(raw, dict):
        return {}
    context = {}
    for key in ("message_id", "chunk_index", "chunk_count"):
        value = raw.get(key)
        if key == "message_id":
            text = str(value or "")
            if text:
                context[key] = text[:160]
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            context[key] = int(value)
    return context


def _timing_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _timing_event(enabled, event, context, **fields):
    if not enabled:
        return
    record = {"event": event, "timestamp": _timing_timestamp(), **context, **fields}
    print("[timing] " + json.dumps(record, ensure_ascii=True, separators=(",", ":")), flush=True)


def _timing_header(value):
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(encoded).decode("ascii")


class Handler(BaseHTTPRequestHandler):
    server_version = "KurisuVoice/1.0"

    def log_message(self, fmt, *args):
        print(f"[api] {self.address_string()} {fmt % args}", flush=True)

    def end_headers(self):
        origin = self.headers.get("Origin", "")
        if trusted_extension_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header(
                "Access-Control-Expose-Headers",
                "X-Irodori-Timing-Lock-Wait-Ms, X-Irodori-Timing-Lock-Hold-Ms, X-Irodori-Timing-Diagnostics, X-Irodori-Engine, X-Irodori-Model",
            )
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        super().end_headers()

    def _authorize_local_request(self, require_extension_origin=False):
        expected_port = int(STATE.api["port"])
        host = str(self.headers.get("Host", "")).strip().lower()
        allowed_hosts = {f"127.0.0.1:{expected_port}", f"localhost:{expected_port}"}
        if host not in allowed_hosts:
            self._send_json(403, {"error": "host_forbidden"})
            return False
        origin = self.headers.get("Origin", "")
        if origin and not trusted_extension_origin(origin):
            self._send_json(403, {"error": "origin_forbidden"})
            return False
        if require_extension_origin and not trusted_extension_origin(origin):
            self._send_json(403, {"error": "extension_origin_required"})
            return False
        return True

    def do_OPTIONS(self):
        if not self._authorize_local_request(require_extension_origin=True):
            return
        requested_method = str(self.headers.get("Access-Control-Request-Method", "")).upper()
        if requested_method not in {"GET", "POST"}:
            self._send_json(403, {"error": "preflight_method_forbidden"})
            return
        requested_headers = {
            item.strip().lower()
            for item in str(self.headers.get("Access-Control-Request-Headers", "")).split(",")
            if item.strip()
        }
        if not requested_headers.issubset({"content-type"}):
            self._send_json(403, {"error": "preflight_header_forbidden"})
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_json(self, status, value):
        body = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _request_json(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        limit = 96 * 1024 * 1024 if urlparse(self.path).path.rstrip("/") == "/voices/import" else 2_000_000
        if length <= 0 or length > limit:
            raise ValueError("JSON本文が空、または大きすぎます。")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSONオブジェクトを送信してください。")
        return value

    def _input(self, payload):
        text = str(payload.get("input", payload.get("text", "")))
        if not text.strip():
            raise ValueError("inputが空です。")
        if len(text) > int(STATE.api["max_input_chars"]):
            raise ValueError(f"inputは{STATE.api['max_input_chars']}文字以内にしてください。")
        return text

    @staticmethod
    def _engine_name(payload=None, query=None):
        payload = payload if isinstance(payload, dict) else {}
        query = query if isinstance(query, dict) else {}
        value = payload.get("engine") or payload.get("mode") or payload.get("engine_mode")
        if value is None:
            value = (query.get("engine") or ["standard"])[0]
        return normalize_engine(value)

    def do_GET(self):
        if not self._authorize_local_request():
            return
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        try:
            if path == "/capabilities":
                self._send_json(200, api_capabilities())
            elif path == "/voices/state":
                self._send_json(200, voice_state())
            elif path in {"/health", "/v1/health"}:
                requested_engine = self._engine_name(query=query)
                _, engine = STATE.engine(engine_name=requested_engine)
                backend_ready = bool(engine.backend.health())
                self._send_json(200 if backend_ready else 503, {
                    "status": "ok" if backend_ready else "unavailable",
                    "backend": "audio.cpp",
                    "device": STATE.device,
                    "host": "127.0.0.1",
                    "port": STATE.api["port"],
                    "engine": requested_engine,
                    "model": STATE.model(requested_engine),
                    "engines": {
                        name: {"label": ENGINE_LABELS[name], "model": STATE.model(name), "endpoint": f"http://127.0.0.1:{STATE.api['port']}"}
                        for name in ENGINE_LABELS
                    },
                    "model_lifecycle": "resident",
                    "backend_ready": backend_ready,
                })
            elif path in {"/voices", "/v1/voices"}:
                self._send_json(200, {"voices": STATE.voices.list()})
            elif path in {"/settings/long-text", "/v1/settings/long-text"}:
                _, profile = STATE.profile(engine_name=self._engine_name(query=query))
                self._send_json(200, {
                    "enabled": True,
                    "target_seconds": 25,
                    "estimated_chars_per_second": 4.8,
                    "max_chars": int(profile.get("storyteller_target_chars") or profile.get("target_chars") or 120),
                    "priorities": ["。！？", "、", "改行", "最大文字数"],
                    "preserve_dialogue": True,
                    "keep_character_tags_separate": True,
                    "processor": "kurisu_voice/planner.py",
                })
            elif path == "/v1/speakers":
                _, profile = STATE.profile()
                folder = Path(profile["speaker_voice_dir"])
                speakers = [{"id": item.stem, "file": item.name} for item in sorted(folder.rglob("*.wav"))] if folder.is_dir() else []
                self._send_json(200, {"speakers": speakers})
            elif path == "/v1/profiles":
                self._send_json(200, {"profiles": list(STATE.profiles), "selected": STATE.config.get("chunk_profile_name")})
            else:
                self._send_json(404, {"error": "not_found"})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def do_POST(self):
        # Require a browser-extension Origin for every POST, not just Voice
        # administration. This covers synthesis, reload and shutdown alike.
        # Host validation also prevents DNS-rebinding requests from web pages.
        if not self._authorize_local_request(require_extension_origin=True):
            return
        path = urlparse(self.path).path.rstrip("/")
        try:
            payload = self._request_json()
            if path == "/shutdown" or path in {"/voices/import", "/voices/update", "/voices/delete"}:
                if path == "/shutdown":
                    stop_api(self)
                else:
                    if API_STOPPING.is_set():
                        self._send_json(503, {"error": "API is stopping"})
                        return
                    self._send_json(200, voice_change(path.rsplit("/", 1)[-1], payload))
                return
            if API_STOPPING.is_set():
                self._send_json(503, {"error": "API is stopping"})
                return
            if path == "/v1/reload":
                STATE.reload()
                self._send_json(200, {"status": "reloaded"})
                return
            text = self._input(payload)
            speaker_tag_policy = str(payload.get("speaker_tag_policy") or "profile").lower()
            if speaker_tag_policy not in {"profile", "preserve"}:
                raise ValueError("speaker_tag_policyはprofileまたはpreserveを指定してください。")
            requested_engine = self._engine_name(payload=payload)
            selected, engine = STATE.engine(payload.get("profile"), payload.get("voice"), speaker_tag_policy, requested_engine)
            if path == "/v1/plan":
                trace = {}
                chunks = engine.plan(text, payload.get("split_options"), trace=trace)
                _log_planner_trace(path, selected, speaker_tag_policy, trace)
                self._send_json(200, {"profile": selected, "engine": requested_engine, "model": STATE.model(requested_engine), "speaker_tag_policy": speaker_tag_policy, "chunks": chunks})
            elif path in {"/tts", "/v1/tts", "/v1/synthesize", "/v1/audio/speech"}:
                timing_enabled = bool(payload.get("timing_debug"))
                timing_context = _safe_timing_context(payload)
                wait_started = time.perf_counter() if timing_enabled else None
                wait_started_at = _timing_timestamp() if timing_enabled else None
                _timing_event(timing_enabled, "synthesis_lock_wait_start", timing_context)
                acquired = SYNTHESIS_LOCK.acquire(timeout=600)
                if not acquired:
                    wait_ms = round((time.perf_counter() - wait_started) * 1000.0, 1) if wait_started is not None else None
                    _timing_event(timing_enabled, "synthesis_lock_timeout", timing_context, elapsed_ms=wait_ms)
                    self._send_json(503, {"error": "busy", "message": "音声生成キューがタイムアウトしました。"})
                    return
                acquired_at = time.perf_counter() if timing_enabled else None
                acquired_at_timestamp = _timing_timestamp() if timing_enabled else None
                wait_ms = round((acquired_at - wait_started) * 1000.0, 1) if acquired_at is not None else None
                _timing_event(timing_enabled, "synthesis_lock_acquired", timing_context, elapsed_ms=wait_ms)
                trace_path = None
                trace_start = 0
                if timing_enabled and requested_engine == "voice_stable":
                    trace_path = stable_trace_path(ROOT)
                    trace_start = trace_offset(trace_path)
                body = None
                stable_diagnostics = {"attempts": [], "session_wall_ms": None, "trace_end": None, "stable_markers": False}
                try:
                    options = {}
                    if "seed" in payload:
                        options["seed"] = int(payload["seed"])
                    if "num_inference_steps" in payload or "num_steps" in payload:
                        options["num_steps"] = int(payload.get("num_inference_steps", payload.get("num_steps")))
                    if isinstance(payload.get("split_options"), dict):
                        options["split_options"] = payload["split_options"]
                    # Preserve profile/voice-id callers while allowing an
                    # explicit WAV or speaker embedding for this request.
                    reference = payload.get("reference")
                    if not isinstance(reference, dict):
                        reference = {}
                    voice_ref = payload.get("voice_ref")
                    speaker_embedding = payload.get("speaker_embedding_path")
                    if not voice_ref and reference.get("path_mode") == "wav":
                        voice_ref = reference.get("path")
                    if not speaker_embedding and reference.get("path_mode") == "speaker_embedding":
                        speaker_embedding = reference.get("path")
                    if voice_ref and speaker_embedding:
                        raise ValueError("reference WAV and speaker embedding cannot be used together")
                    if voice_ref:
                        options["voice_ref_path"] = str(voice_ref)
                    if speaker_embedding:
                        options["speaker_embedding_path"] = str(speaker_embedding)
                    trace = {}
                    try:
                        result = engine.synthesize(text, options, trace=trace)
                    finally:
                        _log_planner_trace(path, selected, speaker_tag_policy, trace)
                    body = result.wav_path.read_bytes()
                    result.wav_path.unlink(missing_ok=True)
                finally:
                    hold_ms = round((time.perf_counter() - acquired_at) * 1000.0, 1) if acquired_at is not None else None
                    released_at_timestamp = _timing_timestamp() if timing_enabled else None
                    SYNTHESIS_LOCK.release()
                    _timing_event(timing_enabled, "synthesis_lock_released", timing_context, elapsed_ms=hold_ms)
                if timing_enabled and trace_path is not None:
                    stable_diagnostics = collect_trace(trace_path, trace_start)
                timing_diagnostics = {
                    "timing_context": timing_context,
                    "lock": {
                        "wait_ms": wait_ms,
                        "hold_ms": hold_ms,
                        "wait_start": wait_started_at,
                        "acquired": acquired_at_timestamp,
                        "released": released_at_timestamp,
                    },
                    "stable": stable_diagnostics,
                }
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition", 'inline; filename="kurisu_voice.wav"')
                self.send_header("X-Kurisu-Profile", selected.encode("ascii", "ignore").decode("ascii") or "profile")
                self.send_header("X-Irodori-Engine", requested_engine)
                self.send_header("X-Irodori-Model", STATE.model(requested_engine))
                if timing_enabled:
                    self.send_header("X-Irodori-Timing-Lock-Wait-Ms", str(wait_ms if wait_ms is not None else ""))
                    self.send_header("X-Irodori-Timing-Lock-Hold-Ms", str(hold_ms if hold_ms is not None else ""))
                    self.send_header("X-Irodori-Timing-Diagnostics", _timing_header(timing_diagnostics))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send_json(404, {"error": "not_found"})
        except KeyError as exc:
            self._send_json(404, {"error": str(exc).strip("'")})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})


def main():
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()), encoding="ascii")
    server = ThreadingHTTPServer(("127.0.0.1", STATE.api["port"]), Handler)
    print(f"[Kurisu Voice API] http://127.0.0.1:{STATE.api['port']}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        PID_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
