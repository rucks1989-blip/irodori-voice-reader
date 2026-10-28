import json
import tempfile
import unittest
from pathlib import Path

from kurisu_voice.voices import VoiceRegistry


class VoiceRegistryTests(unittest.TestCase):
    def test_disabled_auto_voices_are_hidden_but_config_voice_remains(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "voice_refs").mkdir()
            (root / "voice_refs" / "hidden.wav").write_bytes(b"hidden")
            (root / "voice_refs" / "visible.wav").write_bytes(b"visible")
            (root / "irodori_embed_profiles").mkdir()
            (root / "irodori_embed_profiles" / "embed.speaker.safetensors").write_bytes(b"embed")
            config = root / "config" / "voices.json"
            config.parent.mkdir()
            config.write_text(json.dumps({
                "version": 1,
                "disabled_auto_voices": ["hidden", "embed"],
                "voices": [{"id": "hidden", "name": "Manual hidden", "reference_wav": "voice_refs/hidden.wav"}],
            }), encoding="utf-8")

            voices = {voice["id"]: voice for voice in VoiceRegistry(root, config).list()}

            self.assertEqual(set(voices), {"hidden", "visible"})
            self.assertEqual(voices["hidden"]["source"], "config")
            self.assertEqual(voices["visible"]["source"], "auto")


if __name__ == "__main__":
    unittest.main()
