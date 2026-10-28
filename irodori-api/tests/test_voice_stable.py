import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from kurisu_voice.backends.audiocpp import AudioCppBackend
from kurisu_voice.engine import KurisuVoice
from kurisu_voice.profiles import default_profile


def silent_wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 240)
    return output.getvalue()


class FakeResponse:
    ok = True
    status_code = 200
    text = ""
    content = b"RIFF" + b"\x00" * 40


class RecordingBackend:
    def __init__(self):
        self.calls = []

    def synthesize_chunk(self, text, voice_ref="", options=None):
        self.calls.append((text, voice_ref, dict(options or {})))
        return silent_wav()


class VoiceStableTransportTests(unittest.TestCase):
    def test_unicode_speaker_embedding_keeps_direct_suffix(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "日本語" / "voice.speaker.safetensors"
            source.parent.mkdir()
            source.write_bytes(b"speaker embedding")
            cache_root = root / "cache"
            with patch("kurisu_voice.backends.audiocpp.tempfile.gettempdir", return_value=str(cache_root)), patch(
                "kurisu_voice.backends.audiocpp.requests.post", return_value=FakeResponse()
            ) as post:
                AudioCppBackend("http://127.0.0.1:8091/v1/audio/speech", "stable").synthesize_chunk(
                    "テスト", options={"speaker_embedding_path": str(source)}
                )

            payload = post.call_args.kwargs["json"]
            forwarded = Path(payload["speaker_embedding_path"])
            self.assertTrue(forwarded.name.endswith(".speaker.safetensors"))
            self.assertFalse(forwarded.name.endswith(".wav"))
            self.assertEqual(forwarded.read_bytes(), source.read_bytes())
            self.assertNotIn("voice_ref", payload)

    def test_explicit_wav_and_embedding_are_forwarded_without_conversion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            embedding = root / "voice.speaker.safetensors"
            wav = root / "reference.wav"
            embedding.write_bytes(b"speaker embedding")
            wav.write_bytes(b"wav")
            profile = default_profile()
            profile["speaker_switch_enabled"] = False

            backend = RecordingBackend()
            engine = KurisuVoice(root, profile, backend)
            result = engine.synthesize("短いテスト。", {"speaker_embedding_path": str(embedding)})
            result.wav_path.unlink(missing_ok=True)
            self.assertEqual(backend.calls[0][1], "")
            self.assertEqual(backend.calls[0][2]["speaker_embedding_path"], str(embedding))

            backend = RecordingBackend()
            engine = KurisuVoice(root, profile, backend)
            result = engine.synthesize("短いテスト。", {"voice_ref_path": str(wav)})
            result.wav_path.unlink(missing_ok=True)
            self.assertEqual(backend.calls[0][1], str(wav))
            self.assertNotIn("speaker_embedding_path", backend.calls[0][2])

    def test_shared_backend_config_includes_stable_model(self):
        config_path = Path(__file__).resolve().parents[1] / "config" / "audio_cpp_server.shared.example.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["port"], 8091)
        model_ids = {model["id"] for model in config["models"]}
        self.assertIn("irodori-500m-v3-q8", model_ids)
        self.assertIn("irodori-500m-v3-q8-voice-stable-speaker", model_ids)
        self.assertFalse(config["lazy_load"])
        for model in config["models"]:
            self.assertEqual(model["session_options"]["irodori_tts.reference_cache_slots"], "1")


if __name__ == "__main__":
    unittest.main()
