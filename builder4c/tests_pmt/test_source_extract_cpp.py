"""Casos C++ modernos en el extractor (pmt.source_extract): raw strings y
separadores de digitos (C++14), que antes desincronizaban el enmascarado de
cadenas y hacian que no se extrajera la funcion.

    python3 -m unittest discover -s tests_pmt -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pmt.source_extract import (  # noqa: E402
    extract_enclosing_method,
    mask_comments_and_strings,
)

RAW_STRING_CPP = '''\
TEST_CASE("raw strings", "[x]") {
    CHECK(convert(""_sr) == R"("")"s);
    CHECK(convert("{"_sr) == R"xy({ "a" })xy"s);
}

int after(int a) {
    return a + 1;
}
'''

DIGITS_CPP = '''\
int big() {
    int n = 1'000'000;
    unsigned m = 0xFF'FF;
    char c = '{';
    return n + m + c;
}

int next(int a) {
    return a;
}
'''


class ModernCppExtraction(unittest.TestCase):
    def test_mask_raw_string_keeps_offsets(self):
        src = 'x = R"xy(a"b)c)xy"; y = 1;'
        masked = mask_comments_and_strings(src)
        self.assertEqual(len(masked), len(src))
        self.assertTrue(masked.endswith('"; y = 1;'))
        self.assertNotIn("a", masked[4:18])

    def test_raw_string_function_extracted(self):
        e = extract_enclosing_method(RAW_STRING_CPP, 2)
        self.assertIsNotNone(e)
        self.assertEqual((e.start_line, e.end_line), (1, 3))
        e = extract_enclosing_method(RAW_STRING_CPP, 7)
        self.assertEqual(e.lines[0], "int after(int a) {")

    def test_digit_separators_are_not_char_literals(self):
        masked = mask_comments_and_strings(DIGITS_CPP)
        self.assertIn("1'000'000", masked)
        self.assertIn("0xFF'FF", masked)
        e = extract_enclosing_method(DIGITS_CPP, 3)
        self.assertEqual((e.start_line, e.end_line), (1, 5))
        e = extract_enclosing_method(DIGITS_CPP, 9)
        self.assertEqual(e.lines[0], "int next(int a) {")


MACRO_H = """\
#define TEST_LEVEL_FILTER(log_level, expected, action_fn)                     \\
    static int s_filter_##log_level##_##action_fn(void *ctx) {                \\
        (void)ctx;                                                            \\
        return do_log_test(log_level, expected, action_fn);                   \\
    }                                                                         \\
    AWS_TEST_CASE(test_filter_##log_level##_##action_fn, s_filter_##log_level##_##action_fn);
"""

MACRO_C = """\
#include "util.h"

static void s_all(int level) {
    log_all(level);
}

TEST_LEVEL_FILTER(AWS_LL_NONE, "", s_all)
"""


class MacroGeneratedTests(unittest.TestCase):
    def test_macro_test_is_indexed_and_expanded(self):
        import tempfile
        from pmt.test_source_map import TestIndex, resolve_test, CtestTest
        from pmt.dataset import test_code_from_source
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "tests").mkdir()
            (repo / "tests" / "util.h").write_text(MACRO_H, encoding="utf-8")
            (repo / "tests" / "filter_test.c").write_text(MACRO_C, encoding="utf-8")
            idx = TestIndex(repo)
            name = "test_filter_AWS_LL_NONE_s_all"
            self.assertIn(name, idx.macro_generated)
            ts = resolve_test(name, CtestTest(name, ["/b/tests", name]), idx)
            self.assertEqual((ts.strategy, ts.granularity, ts.file, ts.line),
                             ("aws_test_macro", "macro", "tests/filter_test.c", 7))
            code = test_code_from_source(repo, ts) or []
            self.assertEqual(code[0], 'TEST_LEVEL_FILTER(AWS_LL_NONE, "", s_all)')
            self.assertIn("static int s_filter_AWS_LL_NONE_s_all(void *ctx) {", code)
            self.assertIn('return do_log_test(AWS_LL_NONE, "", s_all);', code)
            self.assertIn("static void s_all(int level) {", code)
            self.assertIn("log_all(level);", code)


if __name__ == "__main__":
    unittest.main()
