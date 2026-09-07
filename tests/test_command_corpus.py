import json
import unittest
from pathlib import Path
from app.command_corpus import expand_persian_command_cases


class PersianCommandCorpusTests(unittest.TestCase):
    def test_corpus_has_broad_coverage_and_valid_shape(self):
        path=Path(__file__).parent/"fixtures"/"management_commands_fa.json"
        cases=json.loads(path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cases),35)
        categories={case["category"] for case in cases}
        self.assertTrue({"task_create","task_update","meeting_create","meeting_move","meeting_swap",
                         "query","availability","multi","reference","self_assignment","notification"}<=categories)
        for case in cases:
            self.assertTrue(case["text"].strip())
            self.assertTrue(case["operations"])

    def test_expanded_corpus_has_300_unique_natural_variants(self):
        path=Path(__file__).parent/"fixtures"/"management_commands_fa.json"
        seeds=json.loads(path.read_text(encoding="utf-8"))
        cases=expand_persian_command_cases(seeds,300)
        self.assertEqual(len(cases),300)
        self.assertEqual(len({case["text"] for case in cases}),300)
        self.assertTrue(any(" رو " in case["text"] for case in cases))
        self.assertTrue(any("میخوام" in case["text"] for case in cases))


if __name__=="__main__": unittest.main()
