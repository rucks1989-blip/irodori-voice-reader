"""Chrome Native Messaging adapter for the shared Irodori host implementation."""
from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import irodori_native_host as firefox_host

DEFAULT_EXTENSION_ID = "aeaichhflmacmifblidbpojgfemjijlb"


def _diagnostic(message: str) -> None:
    """Write ASCII-only diagnostics to stderr without touching Native Messaging stdout."""
    try:
        print(f"[irodori-chrome-native-host] {message}", file=sys.stderr, flush=True)
    except Exception:
        pass


def configured_extension_id() -> str:
    try:
        value = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))
        candidate = str(value.get("chrome_extension_id") or "").strip()
        if candidate:
            return candidate
    except (OSError, ValueError):
        pass
    return DEFAULT_EXTENSION_ID


VOICE_TARGET_NAMES = {
    "reference_wav": "reference.wav",
    "speaker_embedding": "speaker.safetensors",
    "safetensors": "model.safetensors",
}


def _voice_import_target(config: dict, message: dict) -> tuple[Path, str]:
    root = Path(config["root"]).resolve()
    voice_id = str(message.get("id") or "").strip()
    target_name = VOICE_TARGET_NAMES.get(str(message.get("voiceType") or ""), "unknown")
    target = (root / "IrodoriVoiceReader" / "voices" / voice_id / target_name).resolve()
    try:
        relative = target.relative_to(root).as_posix()
    except ValueError:
        relative = "<outside-configured-root>"
    return target, relative


def _log_voice_import_received(message: dict) -> None:
    encoded = str(message.get("dataBase64") or "")
    _diagnostic(
        "voice_import_received "
        f"keys={ascii(sorted(message.keys()))} "
        f"id={ascii(str(message.get('id') or ''))} "
        f"voice_type={ascii(str(message.get('voiceType') or ''))} "
        f"file_name={ascii(str(message.get('fileName') or ''))} "
        f"data_base64_present={bool(encoded)} data_base64_length={len(encoded)}"
    )
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except Exception as error:
        _diagnostic(f"voice_import_base64_decode success=False base64_length={len(encoded)} error={ascii(str(error))}")
        return
    _diagnostic(f"voice_import_base64_decode success=True base64_length={len(encoded)} bytes={len(decoded)}")


def _log_voice_import_post_state(config: dict, message: dict, response: dict | None) -> None:
    target, relative = _voice_import_target(config, message)
    try:
        size = target.stat().st_size if target.is_file() else 0
        file_success = target.is_file()
    except OSError as error:
        size = 0
        file_success = False
        _diagnostic(f"voice_import_file_save_stat success=False error={ascii(str(error))}")
    _diagnostic(f"voice_import_file_save success={file_success} path={ascii(relative)} bytes={size}")

    voices_path = Path(config["root"]) / "config" / "voices.json"
    field = {"reference_wav": "reference_wav", "speaker_embedding": "speaker_embedding", "safetensors": "safetensors"}.get(str(message.get("voiceType") or ""), "")
    voice_id = str(message.get("id") or "").strip()
    try:
        value = json.loads(voices_path.read_text(encoding="utf-8-sig"))
        record = next((item for item in value.get("voices", []) if isinstance(item, dict) and item.get("id") == voice_id), None)
        record_path = str(record.get(field) or "") if record and field else ""
        response_ok = isinstance(response, dict) and response.get("ok") is True
        path_match = record_path == relative
        update_success = response_ok and path_match
        _diagnostic(
            "voice_import_voices_json_update "
            f"success={update_success} path=config/voices.json exists=True "
            f"record_present={record is not None} path_match={path_match}"
        )
    except (OSError, ValueError, AttributeError, TypeError) as error:
        _diagnostic(f"voice_import_voices_json_update success=False path=config/voices.json exists={voices_path.is_file()} error={ascii(str(error))}")


def _install_voice_import_diagnostics() -> None:
    original_read_message = firefox_host.read_message
    original_handle = firefox_host.handle
    original_reload_api = firefox_host._reload_api

    def reload_api_with_diagnostics(config: dict) -> dict:
        try:
            result = original_reload_api(config)
            _diagnostic(
                "voice_import_reload success=True "
                f"status={result.get('status')} applied={result.get('applied')}"
            )
            return result
        except Exception as error:
            _diagnostic(f"voice_import_reload success=False error={ascii(str(error))}")
            raise

    def read_message_with_diagnostics() -> dict | None:
        message = original_read_message()
        if isinstance(message, dict) and message.get("command") == "voice_import":
            _log_voice_import_received(message)
        return message

    def handle_with_diagnostics(message: dict, config: dict) -> dict:
        if message.get("command") != "voice_import":
            return original_handle(message, config)
        _, relative = _voice_import_target(config, message)
        _diagnostic(f"voice_import_save_destination path={ascii(relative)}")
        firefox_host._reload_api = reload_api_with_diagnostics
        try:
            response = original_handle(message, config)
        except Exception as error:
            _diagnostic(f"voice_import_handle_exception error={ascii(str(error))}")
            _log_voice_import_post_state(config, message, {"ok": False})
            raise
        _diagnostic(f"voice_import_handle_response ok={response.get('ok') if isinstance(response, dict) else None} keys={ascii(sorted(response.keys())) if isinstance(response, dict) else []}")
        _log_voice_import_post_state(config, message, response if isinstance(response, dict) else None)
        return response

    firefox_host.read_message = read_message_with_diagnostics
    firefox_host.handle = handle_with_diagnostics


def main() -> int:
    extension_id = configured_extension_id()
    origin = sys.argv[1] if len(sys.argv) > 1 else ""
    expected = f"chrome-extension://{extension_id}"
    normalized_origin = origin.strip().rstrip("/")
    _diagnostic(f"argv={ascii(sys.argv)}")
    _diagnostic(
        "origin="
        f"{ascii(origin)} normalized={ascii(normalized_origin)} "
        f"expected={ascii(expected)} match={normalized_origin == expected}"
    )
    if normalized_origin != expected:
        try:
            firefox_host.write_message({"ok": False, "error": "許可されていないChrome拡張機能です。", "helperVersion": firefox_host.HOST_VERSION})
        except Exception:
            pass
        return 1
    # The shared implementation already validates argv[2] against the fixed
    # Firefox ID. Present the same internal value after validating Chrome's
    # origin above; Firefox registration and files are not modified.
    sys.argv[:] = [sys.argv[0], origin, firefox_host.EXPECTED_EXTENSION_ID]
    _install_voice_import_diagnostics()
    return firefox_host.main()


if __name__ == "__main__":
    raise SystemExit(main())
