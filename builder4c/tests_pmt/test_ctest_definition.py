"""Tests sin caso concreto (Catch2 --list-tests, -h...): su codigo es la
definicion en CMake; y el re-enlace nunca empeora un enlace previo.

    python3 -m unittest discover -s tests_pmt -v
"""

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pmt.dataset import test_code_from_source  # noqa: E402
from pmt import test_source_map as tsm  # noqa: E402

CMAKE = """\
add_test(NAME RunTests COMMAND $<TARGET_FILE:SelfTest> --order rand)
add_test(NAME VersionCheck COMMAND $<TARGET_FILE:SelfTest> -h)
set_tests_properties(VersionCheck PROPERTIES PASS_REGULAR_EXPRESSION "Catch2 v${PROJECT_VERSION}")

add_test(NAME Other COMMAND $<TARGET_FILE:SelfTest> --list-tags)
set_tests_properties(Other PROPERTIES PASS_REGULAR_EXPRESSION "tags")
"""


def ctest_json(repo: Path) -> str:
    return json.dumps({
        "kind": "ctestInfo",
        "backtraceGraph": {"commands": ["add_test"],
                           "files": [f"{repo.as_posix()}/tests/CMakeLists.txt"],
                           "nodes": [{"file": 0}, {"command": 0, "file": 0, "line": 1},
                                     {"command": 0, "file": 0, "line": 2}]},
        "tests": [
            {"name": "RunTests", "command": ["/b/SelfTest", "--order", "rand"], "backtrace": 1},
            {"name": "VersionCheck", "command": ["/b/SelfTest", "-h"], "backtrace": 2},
        ]})


class CtestDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
        self.ct = tsm.parse_ctest_json(ctest_json(self.repo))
        self.index = tsm.TestIndex(self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def test_backtrace_is_parsed(self):
        self.assertEqual(self.ct["VersionCheck"].def_line, 2)
        self.assertTrue(self.ct["VersionCheck"].def_file.endswith("tests/CMakeLists.txt"))

    def test_cli_test_links_to_its_cmake_definition(self):
        ts = tsm.resolve_test("VersionCheck", self.ct["VersionCheck"], self.index)
        self.assertEqual((ts.strategy, ts.granularity, ts.file, ts.line),
                         ("ctest_definition", "ctest", "tests/CMakeLists.txt", 2))
        code = test_code_from_source(self.repo, ts) or []
        self.assertEqual(code[0], "add_test(NAME VersionCheck COMMAND $<TARGET_FILE:SelfTest> -h)")
        self.assertIn('set_tests_properties(VersionCheck PROPERTIES PASS_REGULAR_EXPRESSION '
                      '"Catch2 v${PROJECT_VERSION}")', code)
        self.assertFalse(any("Other" in c for c in code))
        self.assertEqual(code[-1], "# ctest: /b/SelfTest -h")

    def test_sub_test_never_falls_back_to_suite_definition(self):
        ts = tsm.resolve_test("RunTests/Some case", self.ct["RunTests"], self.index)
        self.assertEqual(ts.strategy, "none")

    def test_relink_never_downgrades(self):
        raw = self.repo / "raw"
        raw.mkdir()
        (raw / "testMap.csv").write_text("TestNo,TestName\n1,ctest.P[RunTests/Some case]\n",
                                         encoding="utf-8")
        (raw / "ctest.json").write_text(ctest_json(self.repo), encoding="utf-8")
        tsm.write_test_sources_csv(raw / "test_sources.csv", {1: tsm.TestSource(
            test="RunTests/Some case", file="tests/x.cpp", line=7, function="Some case",
            granularity="function", strategy="subtest")})
        tsm.main(["--relink", "--repo", str(self.repo), "--ctest-json", str(raw / "ctest.json"),
                  "--tests-from", str(raw / "testMap.csv"), "--out", str(raw)])
        with open(raw / "test_sources.csv", encoding="utf-8", newline="") as fh:
            row = next(csv.DictReader(fh))
        self.assertEqual((row["Strategy"], row["TestFile"], row["TestLine"]),
                         ("subtest", "tests/x.cpp", "7"))


if __name__ == "__main__":
    unittest.main()
