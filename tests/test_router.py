# -*- coding: utf-8 -*-
"""
WorkBuddy Skill Router — 单元测试
"""
import sys
import os
import json
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from skill_router import SkillRouter, SkillFeedbackLearner, tokenize, cosine_similarity


class TestTokenize(unittest.TestCase):
    def test_english(self):
        tokens = tokenize("code review bug")
        self.assertIn("code", tokens)
        self.assertIn("review", tokens)
        self.assertIn("bug", tokens)

    def test_chinese_bigram(self):
        tokens = tokenize("利润表")
        self.assertIn("利润", tokens)
        self.assertIn("润表", tokens)

    def test_chinese_trigram(self):
        tokens = tokenize("财务报表")
        self.assertIn("财务报", tokens)
        self.assertIn("务报表", tokens)

    def test_mixed(self):
        tokens = tokenize("DCF 折现现金流")
        self.assertIn("dcf", tokens)
        self.assertIn("折现", tokens)


class TestCosineSimilarity(unittest.TestCase):
    def test_identical(self):
        v = {"a": 1.0, "b": 2.0}
        self.assertAlmostEqual(cosine_similarity(v, v), 1.0, places=5)

    def test_orthogonal(self):
        v1 = {"a": 1.0}
        v2 = {"b": 1.0}
        self.assertAlmostEqual(cosine_similarity(v1, v2), 0.0, places=5)

    def test_zero_vector(self):
        self.assertEqual(cosine_similarity({}, {"a": 1.0}), 0.0)


class TestSkillRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        index_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "skill_index.json"
        )
        cls.router = SkillRouter(index_path)

    def _assert_routes_to(self, query, expected_skill, history=None, used_skills=None):
        result = self.router.route(query, history=history, used_skills=used_skills)
        self.assertEqual(
            result["skill"], expected_skill,
            f"Query '{query}' expected '{expected_skill}' but got '{result['skill']}' "
            f"(confidence={result['confidence']:.3f})"
        )

    def test_code_review(self):
        self._assert_routes_to("帮我看看这段代码有没有 bug", "code-review")

    def test_stock_query(self):
        self._assert_routes_to("贵州茅台今天股价多少", "neodata-financial-search")

    def test_documentation(self):
        self._assert_routes_to("写一个 README 文档", "documentation")

    def test_git_workflow(self):
        self._assert_routes_to("git commit 信息怎么写", "git-workflow")

    def test_dcf_exact(self):
        self._assert_routes_to("帮我分析下宁德时代的 DCF 估值", "dcf-model")

    def test_macro_data(self):
        # CPI 查询两个金融数据 skill 均合理，允许任意一个命中
        result = self.router.route("最近的 CPI 数据怎么样")
        self.assertIn(
            result["skill"],
            ["neodata-financial-search", "finance-data-retrieval"],
            f"宏观数据查询应路由到金融数据类 skill，实际: {result['skill']}"
        )

    def test_pptx(self):
        self._assert_routes_to("做个 PPT 给我", "pptx")

    def test_xlsx(self):
        self._assert_routes_to("Excel 表格怎么加公式", "xlsx")

    def test_web_research(self):
        self._assert_routes_to("调研一下新能源汽车市场", "web-research")

    def test_comps(self):
        self._assert_routes_to("可比公司估值倍数分析", "comps-analysis")

    def test_memory_consolidation(self):
        self._assert_routes_to("整理一下我的记忆文件", "memory-consolidation")

    def test_prd(self):
        self._assert_routes_to("帮我做个 PRD 文档", "product-management-workflows")

    def test_context_aware(self):
        """上下文感知：历史提到茅台，当前问利润表，应路由到财务数据"""
        history = [
            {"role": "user", "content": "帮我查一下贵州茅台的行情"},
            {"role": "assistant", "content": "茅台今日收盘 1680..."},
        ]
        result = self.router.route(
            "再看看它的利润表",
            history=history,
            used_skills=["neodata-financial-search"]
        )
        # 增强后的 query 应包含实体
        self.assertIn("贵州茅台", result["augmented_query"])
        # action 不应是 fallback
        self.assertNotEqual(result["action"], "fallback")

    def test_fallback_on_empty(self):
        """空 query 应触发 fallback"""
        result = self.router.route("")
        self.assertEqual(result["action"], "fallback")

    def test_all_12_cases_accuracy(self):
        """完整 12 个测试用例准确率应为 100%"""
        test_cases = [
            ("帮我看看这段代码有没有 bug",       "code-review"),
            ("贵州茅台今天股价多少",             "neodata-financial-search"),
            ("写一个 README 文档",              "documentation"),
            ("git commit 信息怎么写",            "git-workflow"),
            ("帮我分析下宁德时代的 DCF 估值",     "dcf-model"),
            # CPI 可路由到任意一个金融数据 skill
            ("最近的 CPI 数据怎么样",            None),
            ("做个 PPT 给我",                   "pptx"),
            ("Excel 表格怎么加公式",             "xlsx"),
            ("调研一下新能源汽车市场",            "web-research"),
            ("可比公司估值倍数分析",             "comps-analysis"),
            ("整理一下我的记忆文件",             "memory-consolidation"),
            ("帮我做个 PRD 文档",               "product-management-workflows"),
        ]
        FINANCIAL_DATA_SKILLS = {"neodata-financial-search", "finance-data-retrieval"}
        correct = 0
        for query, expected in test_cases:
            result = self.router.route(query)
            if expected is None:
                # CPI case: 任意金融数据 skill 均算通过
                hit = result["skill"] in FINANCIAL_DATA_SKILLS
            else:
                hit = result["skill"] == expected
            if hit:
                correct += 1
        self.assertEqual(correct, 12, f"准确率不达标: {correct}/12")


class TestFeedbackLearner(unittest.TestCase):
    def test_on_correction_adds_triggers(self):
        # 使用临时索引文件，仅保留最小 skill 集，避免污染真实数据
        minimal_index = {
            "version": "test",
            "generated_at": "test",
            "description": "test",
            "schema": {},
            "skills": [
                {
                    "id": "finance-data-retrieval",
                    "name": "finance-data-retrieval",
                    "category": "金融数据",
                    "description": "结构化金融数据 API",
                    "triggers": ["股票", "行情"],
                    "path": "",
                    "complexity": 2,
                    "priority": 1,
                }
            ],
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(minimal_index, f, ensure_ascii=False)
            tmp_path = f.name

        try:
            router = SkillRouter(tmp_path)
            learner = SkillFeedbackLearner(router)
            skill_id = "finance-data-retrieval"
            initial_count = 2  # "股票", "行情"

            # 用一个全新词汇触发纠错
            learner.on_correction("查季度净利润增速", skill_id)

            with open(tmp_path, encoding="utf-8") as f:
                updated = json.load(f)
            updated_skill = next(s for s in updated["skills"] if s["id"] == skill_id)
            self.assertGreater(
                len(updated_skill.get("triggers", [])),
                initial_count,
                "纠错后触发词数量应增加"
            )
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
