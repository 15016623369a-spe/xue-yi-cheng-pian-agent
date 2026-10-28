"""Source selection must preserve evidence even for Markdown tables and repeats."""
import copy
import unittest

from video_agent.planning import locate_evidence, validate_knowledge


class SourceSelectionTests(unittest.TestCase):
    def knowledge(self, refs):
        return {"facts": [{"id": "K01", "claim": "只讨论固定示例。",
                           "boundaries": [], "evidence": refs}]}

    def test_table_evidence_preserves_blank_lines_and_markdown(self):
        source = "以下仅为固定示例：\n\n| 输入 | ID |\n|---|---|\n| 🧠 | `[100]` |\n"
        data = self.knowledge([{"source_id": "L1"}, {"source_id": "L5"}])
        original = copy.deepcopy(data)
        resolved = validate_knowledge(locate_evidence(data, source), source)
        refs = resolved["facts"][0]["evidence"]
        self.assertEqual([(r["start_line"], r["end_line"], r["quote"]) for r in refs],
                         [(1, 1, "以下仅为固定示例："), (5, 5, "| 🧠 | `[100]` |")])
        self.assertEqual(data, original)

    def test_repeated_line_can_be_selected_without_guessing_occurrence(self):
        source = "这是重复的原文。\n其他章节说明。\n这是重复的原文。"
        resolved = locate_evidence(self.knowledge([{"source_id": "L3"}]), source)
        validate_knowledge(resolved, source)
        self.assertEqual(resolved["facts"][0]["evidence"][0]["start_line"], 3)

    def test_invalid_or_blank_source_id_never_falls_back_to_supplied_quote(self):
        source = "准确的原文依据。\n\n末尾解释。"
        for selected in ["L0", "L01", "L99", "L2", "L1: 准确的原文依据。", 1, None]:
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                locate_evidence(self.knowledge([{"source_id": selected,
                    "quote": "准确的原文依据。", "start_line": 1, "end_line": 1}]), source)

    def test_selected_id_supplies_source_even_with_wrong_model_copy(self):
        source = "第一行真实依据。\r\n第二行包含 **格式**。"
        resolved = locate_evidence(self.knowledge([{"source_id": "L2", "quote": "幻觉引文",
                                                   "start_line": 99, "end_line": 99}]), source)
        validate_knowledge(resolved, source)
        self.assertEqual(resolved["facts"][0]["evidence"][0]["quote"], "第二行包含 **格式**。")

    def test_legacy_ellipses_and_omitted_blank_lines_still_fail(self):
        source = "第一段原文依据。\n\n第二段原文依据。"
        for quote in ["第一段原文依据。\n第二段原文依据。", "第一段原文依据。...第二段原文依据。"]:
            with self.subTest(quote=quote), self.assertRaises(ValueError):
                validate_knowledge(locate_evidence(self.knowledge([
                    {"quote": quote, "start_line": 1, "end_line": 3}]), source), source)


if __name__ == "__main__":
    unittest.main()
