import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kurisu_voice.backends.audiocpp import AudioCppBackend
from kurisu_voice.backends.standard_audiocpp import StandardAudioCppBackend
from kurisu_voice.standard_voices import StandardVoiceRegistry


class Response:
    ok = True
    content = b"RIFF" + b"\0" * 40


class StandardEmbeddingTests(unittest.TestCase):
    def backend(self, root):
        return StandardAudioCppBackend("http://127.0.0.1:1/v1/audio/speech", "standard", reference_root=root)

    def test_managed_unicode_embedding_keeps_speaker_suffix_and_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "日本語の導入先" / "voices" / "test" / "speaker.safetensors"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"unique embedding contents")
            with patch("kurisu_voice.backends.standard_audiocpp.tempfile.gettempdir", return_value=str(root / "cache")), patch(
                "kurisu_voice.backends.audiocpp.requests.post", return_value=Response()
            ) as post:
                self.backend(root).synthesize_chunk("テスト", options={"speaker_embedding_path": str(source)})
            payload = post.call_args.kwargs["json"]
            forwarded = Path(payload["speaker_embedding_path"])
            self.assertTrue(forwarded.name.endswith(".speaker.safetensors"))
            self.assertEqual(forwarded.read_bytes(), source.read_bytes())
            self.assertNotIn("voice_ref", payload)
            self.assertEqual(source.read_bytes(), b"unique embedding contents")

    def test_references_are_relative_to_each_api_root_not_working_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with patch("kurisu_voice.backends.audiocpp.requests.post", return_value=Response()) as post:
                for name, data in (("install-one", b"first"), ("install-two", b"second")):
                    root = base / name
                    source = root / "voices" / "test.speaker.safetensors"
                    source.parent.mkdir(parents=True)
                    source.write_bytes(data)
                    self.backend(root).synthesize_chunk("テスト", options={"speaker_embedding_path": "voices/test.speaker.safetensors"})
                    self.assertEqual(Path(post.call_args.kwargs["json"]["speaker_embedding_path"]).read_bytes(), data)
                    wav = root / "voices" / "reference.wav"
                    wav.write_bytes(b"reference wav")
                    self.backend(root).synthesize_chunk("テスト", "voices/reference.wav")
                    self.assertEqual(Path(post.call_args.kwargs["json"]["voice_ref"]).resolve(), wav.resolve())
                    self.assertNotIn("speaker_embedding_path", post.call_args.kwargs["json"])

    def test_japanese_native_host_temp_uses_user_private_ascii_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "日本語の導入先" / "speaker.safetensors"
            source.parent.mkdir(); source.write_bytes(b"embedding")
            local = root / "user-data"
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}), patch(
                "kurisu_voice.backends.standard_audiocpp.tempfile.gettempdir", return_value=str(source.parent / "cache/temp")
            ), patch("kurisu_voice.backends.audiocpp.requests.post", return_value=Response()) as post:
                self.backend(root).synthesize_chunk("test", options={"speaker_embedding_path": str(source)})
            copied = Path(post.call_args.kwargs["json"]["speaker_embedding_path"])
            self.assertTrue(copied.is_relative_to(local))
            str(copied).encode("ascii")
            self.assertTrue(copied.name.endswith(".speaker.safetensors"))
            self.assertEqual(copied.read_bytes(), source.read_bytes())

    def test_japanese_native_host_temp_keeps_wav_working(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "日本語" / "reference.wav"
            source.parent.mkdir(); source.write_bytes(b"reference wav")
            with patch.dict(os.environ, {"LOCALAPPDATA": str(root / "private")}), patch(
                "kurisu_voice.backends.standard_audiocpp.tempfile.gettempdir", return_value=str(source.parent)
            ), patch("kurisu_voice.backends.audiocpp.requests.post", return_value=Response()) as post:
                self.backend(root).synthesize_chunk("test", str(source))
            copied = Path(post.call_args.kwargs["json"]["voice_ref"])
            str(copied).encode("ascii")
            self.assertEqual(copied.suffix, ".wav")
            self.assertEqual(copied.read_bytes(), source.read_bytes())

    def test_explicit_cache_setting_and_uppercase_embedding_keep_valid_suffix(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / "VOICE.SPEAKER.SAFETENSORS"; source.write_bytes(b"embedding")
            chosen = root / "chosen-cache"
            backend = StandardAudioCppBackend("http://127.0.0.1:1", "standard", reference_root=root, reference_cache_dir="chosen-cache")
            with patch("kurisu_voice.backends.audiocpp.requests.post", return_value=Response()) as post:
                backend.synthesize_chunk("test", options={"speaker_embedding_path": str(source)})
            copied = Path(post.call_args.kwargs["json"]["speaker_embedding_path"])
            self.assertTrue(copied.is_relative_to(chosen))
            self.assertTrue(copied.name.endswith(".speaker.safetensors"))
            self.assertEqual(copied.read_bytes(), source.read_bytes())

    def test_missing_embedding_has_embedding_error_and_never_calls_engine(self):
        with tempfile.TemporaryDirectory() as folder, patch("kurisu_voice.backends.audiocpp.requests.post") as post:
            with self.assertRaisesRegex(ValueError, "Speaker Embeddingが見つかりません"):
                self.backend(folder).synthesize_chunk("test", options={"speaker_embedding_path": "voices/absent.speaker.safetensors"})
            with self.assertRaisesRegex(ValueError, "参照WAVが見つかりません"):
                self.backend(folder).synthesize_chunk("test", "absent.wav")
            with self.assertRaisesRegex(ValueError, "には.speaker.safetensors"):
                self.backend(folder).synthesize_chunk("test", options={"speaker_embedding_path": "target.safetensors"})
            post.assert_not_called()

    def test_external_sources_are_opt_in_relative_or_absolute_and_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); root = base / "api"; (root / "config").mkdir(parents=True)
            external = base / "trained"; external.mkdir()
            embedding = external / "registered.speaker.safetensors"; embedding.write_bytes(b"training")
            (external / "target.safetensors").write_bytes(b"not an embedding")
            registry = StandardVoiceRegistry(root)
            self.assertEqual(registry.list(), [])
            settings = root / "config" / "voice_sources.json"
            settings.write_text(json.dumps({"speaker_embedding_dirs": ["../trained", str(external)]}), encoding="utf8")
            before = settings.read_bytes()
            voices = registry.list()
            self.assertEqual([v["id"] for v in voices], ["registered"])
            self.assertEqual(Path(registry.get("registered")["path"]), embedding.resolve())
            self.assertEqual(embedding.read_bytes(), b"training")
            self.assertEqual(settings.read_bytes(), before)

    def test_missing_registered_id_cannot_fall_back_to_auto_discovered_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / "config").mkdir(); (root / "irodori_embed_profiles").mkdir()
            (root / "irodori_embed_profiles" / "chosen.speaker.safetensors").write_bytes(b"other voice")
            config = root / "config" / "voices.json"
            config.write_text(json.dumps({"voices": [{"id": "chosen", "speaker_embedding": "missing/speaker.safetensors"}]}), encoding="utf8")
            before = config.read_bytes()
            registry = StandardVoiceRegistry(root)
            self.assertFalse(registry.list()[0]["fileExists"])
            with self.assertRaisesRegex(ValueError, "Voice 'chosen'.*Speaker Embeddingが見つかりません"):
                registry.get("chosen")
            with self.assertRaises(KeyError):
                registry.get("does-not-exist")
            self.assertEqual(config.read_bytes(), before)

    def test_standard_state_uses_fixed_transport_while_stable_keeps_existing_class(self):
        api = importlib.import_module("kurisu_voice_api")
        _, standard = api.STATE.engine(engine_name="standard")
        self.assertIs(type(standard.backend), StandardAudioCppBackend)
        _, stable = api.STATE.engine(engine_name="voice_stable")
        self.assertIs(type(stable.backend), AudioCppBackend)


if __name__ == "__main__":
    unittest.main()
