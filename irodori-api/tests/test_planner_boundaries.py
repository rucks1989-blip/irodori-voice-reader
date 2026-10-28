import unittest
from pathlib import Path

from kurisu_voice.engine import KurisuVoice
from kurisu_voice.planner import build_plan
from kurisu_voice.profiles import default_profile
from kurisu_voice.splitter import natural_cut


ROOT = Path(__file__).resolve().parents[1]


def planner_profile(target=120, hard_limit=140):
    profile = default_profile()
    profile.update({
        "target_chars": target,
        "storyteller_target_chars": target,
        "backtrack_chars": 40,
        "forward_chars": 20,
        "hard_max_chars": hard_limit,
        "preserve_dialogue": True,
        "split_adjacent_dialogue": False,
        "speaker_switch_enabled": False,
    })
    return profile


class PlannerBoundaryTests(unittest.TestCase):
    def test_sentence_before_target_is_selected(self):
        text = "あ" * 114 + "。" + "い" * 80
        cut, reason = natural_cut(text, 120, 40, True, 20, 140)
        self.assertEqual((cut, reason), (115, "sentence"))

    def test_sentence_after_target_is_selected_within_hard_limit(self):
        text = "あ" * 124 + "。" + "い" * 80
        cut, reason = natural_cut(text, 120, 40, True, 20, 140)
        self.assertEqual((cut, reason), (125, "sentence"))

    def test_sentence_priority_beats_nearer_comma(self):
        text = "あ" * 109 + "、" + "い" * 14 + "。" + "う" * 80
        cut, reason = natural_cut(text, 120, 40, True, 20, 140)
        self.assertEqual((cut, reason), (125, "sentence"))

    def test_missing_boundary_uses_hard_cut(self):
        cut, reason = natural_cut("あ" * 200, 120, 40, True, 20, 140)
        self.assertEqual((cut, reason), (120, "hard"))

    def test_dialogue_boundary_is_extended_instead_of_cut_inside(self):
        text = "あ" * 110 + "「" + "う" * 8 + "。" + "え" * 5 + "」" + "お" * 30
        cut, reason = natural_cut(text, 120, 40, True, 20, 140)
        self.assertEqual(reason, "sentence")
        self.assertTrue(text[:cut].endswith("」"))
        self.assertLessEqual(cut, 140)

    def test_hard_max_chars_is_not_exceeded_by_forward_candidate(self):
        text = "あ" * 124 + "。" + "い" * 80
        cut, reason = natural_cut(text, 120, 40, True, 20, 120)
        self.assertEqual((cut, reason), (120, "hard"))
        self.assertLessEqual(cut, 120)

    def test_short_text_stays_one_chunk(self):
        plan = build_plan("短い文章です。", planner_profile(), ROOT)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["text"], "短い文章です。")

    def test_character_tag_boundaries_are_preserved(self):
        profile = planner_profile(target=20, hard_limit=30)
        profile["speaker_switch_enabled"] = True
        text = "【A】" + "あ" * 20 + "。" + "【B】" + "い" * 8 + "。"
        plan = build_plan(text, profile, ROOT)
        self.assertEqual([item.get("speaker_name") for item in plan], ["A", "B"])
        self.assertTrue(all("【" not in item["text"] and "】" not in item["text"] for item in plan))

    def test_extension_sources_use_the_same_common_plan(self):
        text = "あ" * 124 + "。" + "い" * 80
        plans = {
            source: build_plan(text, planner_profile(), ROOT)
            for source in ("chatgpt", "gemini", "selection")
        }
        self.assertEqual(plans["chatgpt"], plans["gemini"])
        self.assertEqual(plans["gemini"], plans["selection"])

    def test_split_options_keep_target_and_hard_max_separate(self):
        engine = KurisuVoice(ROOT, default_profile(), None)
        profile = engine._split_profile("テスト", {
            "enabled": True,
            "target_seconds": 25,
            "max_chars": 140,
            "preserve_dialogue": True,
            "keep_character_tags_separate": True,
        })
        self.assertEqual(profile["target_chars"], 120)
        self.assertEqual(profile["hard_max_chars"], 140)


if __name__ == "__main__":
    unittest.main()
