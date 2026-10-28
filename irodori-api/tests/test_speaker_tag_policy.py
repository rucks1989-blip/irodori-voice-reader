import unittest
from pathlib import Path

from kurisu_voice.planner import build_plan
from kurisu_voice.profiles import default_profile


ROOT = Path(__file__).resolve().parents[1]


class SpeakerTagPolicyTests(unittest.TestCase):
    def test_generic_profile_keeps_existing_speaker_parser_behavior(self):
        profile = default_profile()
        trace = {}
        plan = build_plan("【100点！】そのまま読む。", profile, ROOT, trace=trace)
        self.assertEqual(plan[0]["text"], "そのまま読む。")
        self.assertEqual(plan[0]["speaker_name"], "100点！")
        self.assertEqual(trace["chunks"][0]["text"], "そのまま読む。")

    def test_preserve_policy_keeps_unregistered_brackets_literal(self):
        profile = default_profile()
        profile["speaker_switch_enabled"] = False
        trace = {}
        plan = build_plan("【100点！】そのまま読む。", profile, ROOT, trace=trace)
        self.assertEqual(plan[0]["text"], "【100点！】そのまま読む。")
        self.assertNotIn("speaker_name", plan[0])
        self.assertEqual(trace["chunks"][0]["text"], "【100点！】そのまま読む。")


if __name__ == "__main__":
    unittest.main()
