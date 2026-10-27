import unittest

from revenue_agent import Idea, clamp_score, qa_html, select_idea, slugify


class RevenueAgentTests(unittest.TestCase):
    def test_clamp_score(self):
        self.assertEqual(clamp_score(-1), 0.0)
        self.assertEqual(clamp_score(12), 10.0)
        self.assertEqual(clamp_score("7.5"), 7.5)
        self.assertEqual(clamp_score("bad"), 0.0)

    def test_select_idea_uses_weighted_score(self):
        lower = Idea("low", "", "", [], 3, 3, 3, 3, 3)
        higher = Idea("high", "", "", [], 8, 8, 8, 8, 8)
        self.assertEqual(select_idea([lower, higher]).name, "high")

    def test_slugify(self):
        self.assertEqual(slugify("CSV Cleanup Tool"), "csv-cleanup-tool")
        self.assertEqual(slugify("日本語だけ"), "micro-tool")

    def test_qa_rejects_external_network_behavior(self):
        html = """<!doctype html><html><head><title>x</title></head>
        <body><h1>x</h1><div data-monetization-slot></div><script>
        fetch('https://example.com');
        </script></body></html>""" + (" " * 1300)
        result = qa_html(html)
        self.assertFalse(result["passed"])
        self.assertIn("no_fetch", result["failures"])

    def test_qa_accepts_local_single_file_tool(self):
        html = """<!doctype html><html><head><title>x</title></head>
        <body><h1>x</h1><div data-monetization-slot></div>
        <script>document.body.dataset.ready='1';</script></body></html>""" + (" " * 1300)
        result = qa_html(html)
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()
