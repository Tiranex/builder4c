"""Tests del enlace test ctest -> codigo fuente (pmt.test_source_map) y de su
uso al construir TestMethodCode (pmt.dataset.test_code_from_source).

Ejecutar desde builder4c/:
    python3 -m unittest discover -s tests_pmt -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pmt.test_source_map import (  # noqa: E402
    TestIndex,
    TestSource,
    env_prefix_for,
    expand_tests,
    map_tests,
    parse_ctest_json,
    parse_gtest_listing,
    read_test_sources_csv,
    catch2_escape,
    direct_command,
    parse_catch2_listing,
    prune_catch_sections,
    resolve_test,
    CtestTest,
    source_url,
    split_subtest,
    write_test_sources_csv,
)
from pmt.dataset import test_code_from_source  # noqa: E402

AWS_TEST_C = """\
#include <aws/testing/aws_test_harness.h>

static int s_test_foo_works(struct aws_allocator *allocator, void *ctx) {
    (void)ctx;
    ASSERT_TRUE(1 == 1);
    return AWS_OP_SUCCESS;
}

AWS_TEST_CASE(foo_works_test, s_test_foo_works)

static int s_before(struct aws_allocator *allocator, void *ctx) { return 0; }
static int s_test_fix(struct aws_allocator *allocator, void *ctx) {
    return AWS_OP_SUCCESS;
}
static int s_after(struct aws_allocator *allocator, int setup_res, void *ctx) { return 0; }

AWS_TEST_CASE_FIXTURE(fix_test, s_before, s_test_fix, s_after, NULL)

CBOR_TEST_CASE(cbor_int_test) {
    (void)allocator;
    return AWS_OP_SUCCESS;
}
"""

CATCH_TESTS_CPP = """\
#include <catch2/catch_test_macros.hpp>

TEST_CASE("Factorials are computed", "[factorial]") {
    REQUIRE(Factorial(1) == 1);
    REQUIRE(Factorial(2) == 2);
}

TEST_CASE_METHOD(Fixture, "Method case", "[fixture]") {
    CHECK(true);
}

SCENARIO("vectors can be sized", "[vector]") {
    GIVEN("A vector") {
        REQUIRE(true);
    }
}
"""

CMOCKA_TEST_C = """\
#include "utests.h"

static void
test_a(void **state)
{
    (void)state;
    assert_int_equal(1, 1);
}

static void test_b(void **state) {
    (void)state;
}

int
main(void)
{
    const struct CMUnitTest tests[] = {
        UTEST(test_a),
        cmocka_unit_test_setup_teardown(test_b, setup, teardown),
    };
    return cmocka_run_group_tests(tests, NULL, NULL);
}
"""

GTEST_CC = """\
#include <gtest/gtest.h>

TEST(Suite, Name) {
    EXPECT_EQ(1, 1);
}

TEST_F(Fix, Other) {
    EXPECT_TRUE(true);
}
"""

NUTS_TEST_C = """\
#include <nuts.h>

void test_bus_identity(void) {
    NUTS_PASS(0);
}

NUTS_TESTS = {
    { "bus identity", test_bus_identity },
    { NULL, NULL },
};
"""

CTEST_JSON = {
    "kind": "ctestInfo",
    "version": {"major": 1, "minor": 0},
    "backtraceGraph": {"commands": [], "files": [], "nodes": []},
    "tests": [
        {"name": "foo_works_test",
         "command": ["/b/tests/aws-tests", "foo_works_test"],
         "properties": [{"name": "WORKING_DIRECTORY", "value": "/b/tests"}]},
        {"name": "fix_test", "command": ["/b/tests/aws-tests", "fix_test"]},
        {"name": "cbor_int_test", "command": ["/b/tests/aws-tests", "cbor_int_test"]},
        {"name": "Bazel::JustEnv",
         "command": ["/b/tests/SelfTest", "Factorials are computed", "-r", "xml"]},
        {"name": "Fixture::Method", "command": ["/b/tests/SelfTest", "Method case"]},
        {"name": "RunTests", "command": ["/b/tests/SelfTest", "--order", "rand"]},
        {"name": "utest_range", "command": ["/b/tests/utest_range"]},
        {"name": "Suite.Name",
         "command": ["/b/tests/gtest_thing", "--gtest_filter=Suite.Name"]},
        {"name": "nng.sp.bus.bus_test", "command": ["/b/src/sp/bus/bus_test"]},
        {"name": "ApprovalTests",
         "command": ["/usr/bin/python3", "{repo}/tools/scripts/approvalTests.py", "/b/tests/SelfTest"]},
    ],
}


class SourceMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls.tmp.name) / "repo"
        files = {
            "tests/foo_test.c": AWS_TEST_C,
            "tests/SelfTest/Misc.tests.cpp": CATCH_TESTS_CPP,
            "tests/utests/restriction/test_range.c": CMOCKA_TEST_C,
            "tests/gtest_thing.cc": GTEST_CC,
            "src/sp/bus/bus_test.c": NUTS_TEST_C,
            "src/sp/bus/bus.c": "int bus(void) { return 1; }\n",
            "tools/scripts/approvalTests.py": "print('approvals')\n",
            "build/generated.cpp": 'TEST_CASE("Should be ignored", "[x]") {}\n',
        }
        for rel, text in files.items():
            path = cls.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        text = json.dumps(CTEST_JSON).replace("{repo}", cls.repo.as_posix())
        cls.ctest = parse_ctest_json(text)
        cls.index = TestIndex(cls.repo)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def resolve(self, name):
        return resolve_test(name, self.ctest.get(split_subtest(name)[0]), self.index)

    def test_parse_ctest_json(self):
        self.assertEqual(len(self.ctest), 10)
        self.assertEqual(self.ctest["foo_works_test"].working_dir, "/b/tests")
        self.assertEqual(self.ctest["utest_range"].exe_stem, "utest_range")

    def test_index_ignores_build_dirs(self):
        self.assertNotIn("Should be ignored", self.index.catch)
        self.assertIn("Factorials are computed", self.index.catch)

    def test_aws_test_case(self):
        ts = self.resolve("foo_works_test")
        self.assertEqual((ts.strategy, ts.granularity), ("aws_test_case", "function"))
        self.assertEqual(ts.file, "tests/foo_test.c")
        self.assertEqual(ts.function, "s_test_foo_works")
        self.assertEqual(ts.line, 3)

    def test_aws_fixture_uses_third_argument(self):
        ts = self.resolve("fix_test")
        self.assertEqual(ts.function, "s_test_fix")
        self.assertEqual(ts.line, 12)

    def test_project_macro_with_body(self):
        ts = self.resolve("cbor_int_test")
        self.assertEqual(ts.strategy, "test_macro")
        self.assertEqual((ts.file, ts.line), ("tests/foo_test.c", 19))

    def test_catch_test_case_from_argument(self):
        ts = self.resolve("Bazel::JustEnv")
        self.assertEqual(ts.strategy, "catch_test_case")
        self.assertEqual(ts.function, "Factorials are computed")
        self.assertEqual((ts.file, ts.line), ("tests/SelfTest/Misc.tests.cpp", 3))

    def test_catch_test_case_method(self):
        ts = self.resolve("Fixture::Method")
        self.assertEqual(ts.function, "Method case")
        self.assertEqual(ts.line, 8)

    def test_whole_suite_has_no_link(self):
        ts = self.resolve("RunTests")
        self.assertEqual((ts.strategy, ts.granularity), ("none", "none"))

    def test_gtest_filter(self):
        ts = self.resolve("Suite.Name")
        self.assertEqual(ts.strategy, "gtest_filter")
        self.assertEqual((ts.file, ts.line), ("tests/gtest_thing.cc", 3))

    def test_exe_stem_file_granularity(self):
        ts = self.resolve("utest_range")
        self.assertEqual((ts.strategy, ts.granularity), ("exe_stem", "file"))
        self.assertEqual(ts.file, "tests/utests/restriction/test_range.c")
        ts = self.resolve("nng.sp.bus.bus_test")
        self.assertEqual(ts.file, "src/sp/bus/bus_test.c")

    def test_script_strategy(self):
        ts = self.resolve("ApprovalTests")
        self.assertEqual((ts.strategy, ts.granularity), ("script", "file"))
        self.assertEqual(ts.file, "tools/scripts/approvalTests.py")

    def test_cmocka_expansion_and_subtest_link(self):
        ids = expand_tests(["utest_range", "RunTests"], "cmocka", self.ctest, self.index)
        self.assertEqual(ids, ["utest_range/test_a", "utest_range/test_b", "RunTests"])
        ts = self.resolve("utest_range/test_a")
        self.assertEqual((ts.strategy, ts.granularity), ("subtest", "function"))
        self.assertEqual(ts.function, "test_a")
        # la linea apunta al nombre de la funcion ('static void' va en la
        # anterior); extract_enclosing_method recupera el bloque completo
        self.assertEqual(ts.line, 4)
        self.assertEqual(self.resolve("utest_range/test_b").line, 10)
        # como en el dataset Java, el codigo empieza en la linea del nombre
        lines = test_code_from_source(self.repo, ts) or []
        self.assertEqual(lines[0], "test_a(void **state)")
        self.assertEqual(lines[-1], "assert_int_equal(1, 1);")
        self.assertEqual(env_prefix_for("utest_range/test_a", "cmocka"),
                         "LD_PRELOAD=/usr/local/lib/libcmocka_filter.so "
                         "CMOCKA_TEST_FILTER=test_a ")
        self.assertEqual(env_prefix_for("utest_range", "cmocka"), "")
        self.assertEqual(env_prefix_for("x/Suite.Name", "gtest"), "GTEST_FILTER=Suite.Name ")

    def test_nuts_subtests_are_indexed(self):
        subs = dict(self.index.subtests["src/sp/bus/bus_test.c"])
        self.assertIn("test_bus_identity", subs)

    def test_parse_gtest_listing(self):
        listing = ("Running main() from gmock_main.cc\n"
                   "Suite.\n  Name\n  Other  # GetParam() = 3\n"
                   "Prefix/Param.\n  Case/0\n")
        self.assertEqual(parse_gtest_listing(listing),
                         ["Suite.Name", "Suite.Other", "Prefix/Param.Case/0"])

    def test_csv_roundtrip(self):
        mapped = map_tests(["foo_works_test", "utest_range"], self.ctest, self.index)
        rows = {i + 1: ts for i, ts in enumerate(mapped)}
        path = Path(self.tmp.name) / "test_sources.csv"
        write_test_sources_csv(path, rows)
        back = read_test_sources_csv(path)
        self.assertEqual(back[1].function, "s_test_foo_works")
        self.assertEqual(back[2].granularity, "file")
        self.assertEqual(back[1].command, "/b/tests/aws-tests foo_works_test")

    def test_source_url(self):
        self.assertEqual(
            source_url("https://github.com/awslabs/aws-c-common.git", "abc123",
                       "tests/foo_test.c", 3),
            "https://github.com/awslabs/aws-c-common/blob/abc123/tests/foo_test.c#L3")
        self.assertEqual(source_url("", "abc", "f.c"), "")

    def test_code_extraction_function_and_file(self):
        ts = self.resolve("foo_works_test")
        lines = test_code_from_source(self.repo, ts) or []
        self.assertEqual(lines[0], "static int s_test_foo_works(struct aws_allocator *allocator, void *ctx) {")
        self.assertEqual(lines[-1], "return AWS_OP_SUCCESS;")
        ts = self.resolve("Bazel::JustEnv")
        lines = test_code_from_source(self.repo, ts) or []
        self.assertEqual(lines[0], 'TEST_CASE("Factorials are computed", "[factorial]") {')
        self.assertEqual(len(lines), 3)
        ts = self.resolve("utest_range")
        lines = test_code_from_source(self.repo, ts) or []
        self.assertEqual(lines[0], '#include "utests.h"')
        self.assertIsNone(test_code_from_source(self.repo, TestSource(test="x")))


SECTIONS_CPP = """\
#include <catch2/catch_test_macros.hpp>

TEST_CASE("Failing benchmarks", "[!benchmark][.approvals]") {
    int shared = 1;
    SECTION("throw", "Benchmark that throws an exception") {
        BENCHMARK("Throwing benchmark") {
            throw "just a plain literal, bleh";
        };
    }
    SECTION("assert", "Benchmark that asserts inside") {
        BENCHMARK("Asserting benchmark") {
            REQUIRE(1 == 2);
        };
    }
    SECTION( "outer" ) {
        SECTION( "inner a" ) { REQUIRE(shared == 1); }
        SECTION( "inner b" ) {
            REQUIRE(shared == 2);
        }
    }
    CHECK(shared == 1);
}
"""

CATCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<MatchingTests>
  <TestCase>
    <Name>Factorials are computed</Name>
    <ClassName/>
    <Tags>[factorial]</Tags>
    <SourceInfo>
      <File>/pmt_work/catchorg___Catch2/repo/tests/SelfTest/Misc.tests.cpp</File>
      <Line>3</Line>
    </SourceInfo>
  </TestCase>
  <TestCase>
    <Name>Template - int</Name>
    <ClassName/>
    <Tags>[template]</Tags>
    <SourceInfo>
      <File>/other/place/x.cpp</File>
      <Line>10</Line>
    </SourceInfo>
  </TestCase>
</MatchingTests>
"""


class Catch2GranularityTests(unittest.TestCase):
    def test_parse_listing_relativizes_paths(self):
        rows = parse_catch2_listing("noise\n" + CATCH_XML,
                                    Path("/pmt_work/catchorg___Catch2/repo"))
        self.assertEqual(rows[0], ("Factorials are computed",
                                   "tests/SelfTest/Misc.tests.cpp", 3))
        self.assertEqual(rows[1], ("Template - int", "/other/place/x.cpp", 10))
        self.assertEqual(parse_catch2_listing("not xml", Path("/")), [])

    def test_escape(self):
        self.assertEqual(catch2_escape("a, b"), '"a\\, b"')
        self.assertEqual(catch2_escape('say "hi"'), '"say \\"hi\\""')
        self.assertEqual(catch2_escape("back\\slash"), '"back\\\\slash"')

    def test_direct_command_swaps_build_dir(self):
        ct = CtestTest("RunTests", ["/w/build/tests/SelfTest", "--order", "rand"],
                       "/w/build/tests")
        cmd = direct_command(ct, "a, b", "catch2", 35)
        self.assertEqual(cmd, "cd /w/build/tests && exec timeout -k 5 33 "
                              "/w/build/tests/SelfTest '\"a\\, b\"'")
        cov = direct_command(ct, "x", "catch2", 35, "/w/build", "/w/build-cov")
        self.assertIn("/w/build-cov/tests/SelfTest", cov)
        self.assertTrue(cov.startswith("cd /w/build-cov/tests "))

    def test_prune_single_section(self):
        lines = prune_catch_sections(SECTIONS_CPP, 3, ["throw"])
        self.assertEqual(lines, [
            'TEST_CASE("Failing benchmarks", "[!benchmark][.approvals]") {',
            "int shared = 1;",
            'SECTION("throw", "Benchmark that throws an exception") {',
            'BENCHMARK("Throwing benchmark") {',
            'throw "just a plain literal, bleh";',
            "};",
            "}",
            "CHECK(shared == 1);",
        ])

    def test_prune_nested_section(self):
        lines = prune_catch_sections(SECTIONS_CPP, 3, ["outer", "inner b"])
        joined = "\n".join(lines or [])
        self.assertIn('SECTION( "outer" ) {', joined)
        self.assertIn("REQUIRE(shared == 2);", joined)
        self.assertNotIn("inner a", joined)
        self.assertNotIn("throw", joined)
        self.assertEqual(lines[-1], "CHECK(shared == 1);")

    def test_prune_outer_keeps_all_children(self):
        lines = prune_catch_sections(SECTIONS_CPP, 3, ["outer"])
        joined = "\n".join(lines or [])
        self.assertIn("inner a", joined)
        self.assertIn("inner b", joined)
        self.assertNotIn("assert", joined.replace("Asserting", ""))

    def test_section_resolution_and_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "tests").mkdir()
            (repo / "tests" / "bench.tests.cpp").write_text(SECTIONS_CPP, encoding="utf-8")
            index = TestIndex(repo)
            ct = CtestTest("Benchmarking::FailureReporting::ThrowingBenchmark",
                           ["/b/SelfTest", "Failing benchmarks", "-c", "throw", "-r", "xml"])
            ts = resolve_test(ct.name, ct, index)
            self.assertEqual((ts.strategy, ts.granularity, ts.sections),
                             ("catch_test_case", "section", "throw"))
            lines = test_code_from_source(repo, ts) or []
            self.assertNotIn("REQUIRE(1 == 2);", lines)
            self.assertIn('throw "just a plain literal, bleh";', lines)
            # spec con lista separada por comas (Catch2): "Tracker, ..."
            ct2 = CtestTest("Specs", ["/b/SelfTest", "Failing benchmarks,", "___none___"])
            self.assertEqual(resolve_test(ct2.name, ct2, index).function,
                             "Failing benchmarks")

    def test_listed_subtest_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = TestIndex(Path(tmp))
            index.listed["RunTests/Template - int"] = ("tests/t.cpp", 10, "Template - int")
            ts = resolve_test("RunTests/Template - int", None, index)
            self.assertEqual((ts.strategy, ts.granularity, ts.file, ts.line),
                             ("subtest", "function", "tests/t.cpp", 10))

if __name__ == "__main__":
    unittest.main()
