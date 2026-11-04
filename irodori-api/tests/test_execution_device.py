import importlib
import unittest
from unittest.mock import patch


class ExecutionDeviceTests(unittest.TestCase):
    def test_cpu_config_does_not_report_installed_gpu(self):
        api = importlib.import_module("kurisu_voice_api")
        def config(path):
            if path.name == "runtime.json":
                return {"audio_cpp_shared_config": "config/server.json"}
            return {"backend": "cpu"}
        with patch.object(api, "_read_json", side_effect=config), patch.object(api.subprocess, "run") as run:
            self.assertEqual(api.State._device_name(), "CPU")
            run.assert_not_called()

    def test_cuda_config_reports_detected_gpu(self):
        api = importlib.import_module("kurisu_voice_api")
        with patch.object(api, "_read_json", return_value={"backend": "cuda"}), patch.object(api.subprocess, "run") as run:
            run.return_value.stdout = "Verified NVIDIA GPU\n"
            self.assertEqual(api.State._device_name(), "Verified NVIDIA GPU")

    def test_missing_stable_rejected_before_engine_construction(self):
        import tempfile, json
        from pathlib import Path
        api = importlib.import_module("kurisu_voice_api")
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/"config").mkdir()
            (root/"config/capabilities.json").write_text(json.dumps({"standard":True,"voice_stable":False}))
            with patch.object(api,"ROOT",root):
                with self.assertRaisesRegex(ValueError,"声質安定版はインストールされていない"):
                    api.State.__new__(api.State).engine(engine_name="voice_stable")
