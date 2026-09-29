"""Enlace test de ctest -> codigo fuente del caso de prueba.

En los proyectos reales cada "test" del dataset es un test registrado en
CTest (`ctest -N`). Su nombre no dice donde vive el codigo: puede ser un
`add_test` explicito que lanza un binario con un caso concreto (Catch2,
aws-c-common), un ejecutable entero con decenas de casos (cmocka, googletest,
NUTS) o incluso un script. Este modulo resuelve esa correspondencia con la
informacion que da el propio CTest (`ctest --show-only=json-v1`: comando y
argumentos de cada test) mas un indice de los ficheros de test del repo.

Estrategias, en orden de preferencia:

  aws_test_case    comando `driver <nombre>` y el repo define
                   `AWS_TEST_CASE(<nombre>, fn)`  -> funcion `fn`
  catch_test_case  un argumento coincide con `TEST_CASE("<arg>" ...)`,
                   `SCENARIO(...)`, `TEMPLATE_TEST_CASE(...)`... -> ese bloque
  gtest_filter     `--gtest_filter=Suite.Name` (gtest_discover_tests) o el
                   propio nombre del test es `Suite.Name` -> `TEST(Suite, Name)`
  subtest          id expandido `<test ctest>/<funcion>` (ver test_expansion
                   en mutation_runner: cmocka/gtest) -> esa funcion
  script           el comando es python/bash y un argumento es un fichero
                   del repo -> ese fichero entero
  exe_stem         el ejecutable `utest_binary`, `bus_test`, `os-test`...
                   comparte raiz con un fichero de tests -> fichero entero
  none             sin correspondencia

Granularidad: `function` (una funcion/caso) o `file` (fichero entero).

El resultado se guarda junto al raw estilo Major como `test_sources.csv`
(TestNo,TestName,TestFile,TestLine,TestFunction,Granularity,Strategy,Command)
y `pmt.build_dataset` lo usa para rellenar `TestMethodCode` y para emitir el
fichero `<P>_<v>_test_sources.csv` con el enlace (fichero, linea, URL al
commit) de cada test.

Uso manual, sobre un build CMake ya configurado:
  python3 -m pmt.test_source_map --build-dir /pmt_work/X/build \
      --repo /pmt_work/X/repo --out out/X_1/raw [--tests-from raw/testMap.csv]
"""

import argparse
import csv
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# de mas fina a mas gruesa (re-enlazar nunca sustituye por una peor)
GRANULARITY_RANK = {"section": 5, "function": 4, "macro": 4, "file": 2, "ctest": 1, "none": 0}

TEST_SOURCES_COLUMNS = ["TestNo", "TestName", "TestFile", "TestLine",
                        "TestFunction", "Granularity", "Strategy", "Command",
                        "Sections"]

# shim LD_PRELOAD de la imagen Docker (docker/cmocka_filter.c): la cmocka
# 1.1.7 de Ubuntu no lee ninguna variable de entorno de filtro, solo expone
# cmocka_set_test_filter(); el shim la llama con $CMOCKA_TEST_FILTER
CMOCKA_FILTER_PRELOAD = os.environ.get("PMT_CMOCKA_PRELOAD",
                                       "/usr/local/lib/libcmocka_filter.so")

SRC_EXTS = (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh")
SKIP_DIRS_RE = re.compile(
    r"(^|/)(\.git|build[^/]*|cmake-build[^/]*|third[_-]?party|3rdparty|"
    r"external|extern|deps|vendor|node_modules|\.venv)(/|$)", re.I)
MAX_INDEX_FILE_BYTES = 3_000_000
SUBTEST_SEP = "/"
SCRIPT_INTERPRETERS = {"python", "python3", "python2", "bash", "sh", "zsh",
                       "perl", "ruby", "node", "cmake"}

# AWS_TEST_CASE(name, fn) / AWS_TEST_CASE_FIXTURE(name, before, fn, after, ctx)
_AWS_RE = re.compile(r"\b(AWS_TEST_CASE\w*)\s*\(([^()]*)\)")
# TEST_CASE("nombre"...), TEST_CASE_METHOD(Fixture, "nombre"...), SCENARIO("..."),
# TEMPLATE_TEST_CASE("nombre", "[tags]", tipos...) ...
_CATCH_RE = re.compile(
    r"\b(TEST_CASE\w*|SCENARIO\w*|TEMPLATE_\w*TEST_CASE\w*)\s*\(\s*"
    r"(?:[A-Za-z_][\w:<>]*\s*,\s*)?\"((?:\\.|[^\"\\])*)\"")
_GTEST_RE = re.compile(
    r"\b(TEST|TEST_F|TEST_P|TYPED_TEST|TYPED_TEST_P)\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)")
# #define MACRO(a, b, ...) ... AWS_TEST_CASE(prefijo_##a##_##b, ...) (con \ de continuacion)
_DEFINE_RE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+(\w+)\(([^)]*)\)((?:[^\n]*\\\n)*[^\n]*)", re.M)
_AWS_IN_DEFINE_RE = re.compile(r"\bAWS_TEST_CASE\w*\s*\(\s*([\w#\s]+?)\s*,")
# macros propias de cada proyecto que definen un caso con cuerpo: CBOR_TEST_CASE(x) {
_MACRO_CASE_RE = re.compile(r"\b(\w*TEST\w*)\s*\(\s*(\w+)\s*\)\s*\{")
_CMOCKA_RE = re.compile(r"\b(?:UTEST|cmocka_unit_test\w*)\s*\(\s*(\w+)")
_NUTS_RE = re.compile(r"\{\s*\"[^\"]+\"\s*,\s*(\w+)\s*\}")   # NUTS_TESTS de nng
_TEST_FILE_HINT_RE = re.compile(r"(^|/)(tests?|testing|unittests?|utests?|selftest)(/|$)", re.I)


@dataclass
class TestSource:
    test: str
    file: str = ""
    line: int = 0
    function: str = ""
    granularity: str = "none"   # section | function | file | none
    strategy: str = "none"
    command: str = ""
    sections: str = ""          # Catch2 -c: "throw" o "a > b" (secciones anidadas)

    def row(self, test_no: int) -> List:
        return [test_no, self.test, self.file, self.line, self.function,
                self.granularity, self.strategy, self.command, self.sections]


@dataclass
class CtestTest:
    name: str
    command: List[str]
    working_dir: str = ""
    def_file: str = ""      # fichero y linea del add_test() (backtrace de ctest)
    def_line: int = 0

    @property
    def exe(self) -> str:
        return self.command[0] if self.command else ""

    @property
    def exe_stem(self) -> str:
        stem = Path(self.exe).name
        return stem[:-4] if stem.lower().endswith(".exe") else stem

    def command_str(self) -> str:
        return " ".join(shlex.quote(c) for c in self.command)


# ───────────────────────────── ctest json-v1 ─────────────────────────────

def parse_ctest_json(text: str) -> Dict[str, CtestTest]:
    data = json.loads(text)
    out: Dict[str, CtestTest] = {}
    graph = data.get("backtraceGraph") or {}
    nodes = graph.get("nodes") or []
    files = graph.get("files") or []
    for t in data.get("tests", []):
        wd = ""
        for prop in t.get("properties", []) or []:
            if prop.get("name") == "WORKING_DIRECTORY":
                wd = str(prop.get("value", ""))
        def_file, def_line = "", 0
        bt = t.get("backtrace")
        if isinstance(bt, int) and 0 <= bt < len(nodes):
            node = nodes[bt]
            if isinstance(node.get("file"), int) and node["file"] < len(files):
                def_file = files[node["file"]]
                def_line = int(node.get("line") or 0)
        out[t["name"]] = CtestTest(name=t["name"],
                                   command=list(t.get("command") or []),
                                   working_dir=wd, def_file=def_file,
                                   def_line=def_line)
    return out


def load_ctest_tests(build_dir: Path, timeout: int = 120) -> Tuple[Dict[str, CtestTest], str]:
    """Ejecuta `ctest --show-only=json-v1` y devuelve (tests, json crudo)."""
    r = subprocess.run(["ctest", "--test-dir", str(build_dir), "--show-only=json-v1"],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 or not r.stdout.strip():
        logger.warning("ctest --show-only=json-v1 fallo: %s", r.stderr[-300:])
        return {}, ""
    return parse_ctest_json(r.stdout), r.stdout


# ───────────────────────────── indice del repo ────────────────────────────

class TestIndex:
    """Indice de los casos de prueba definidos en los ficheros del repo."""

    def __init__(self, repo_dir: Path):
        self.repo_dir = Path(repo_dir)
        self.aws: Dict[str, Tuple[str, str, int]] = {}      # nombre -> (file, fn, line macro)
        self.catch: Dict[str, Tuple[str, int]] = {}         # nombre -> (file, line)
        self.gtest: Dict[str, Tuple[str, int]] = {}         # Suite.Name -> (file, line)
        self.macro_cases: Dict[str, Tuple[str, int]] = {}   # nombre -> (file, line)
        # id expandido -> (file, line, nombre) que da el propio binario
        # (Catch2 --list-tests -r xml trae fichero y linea de cada TEST_CASE)
        self.listed: Dict[str, Tuple[str, int, str]] = {}
        # tests generados por macros con ## (aws-c-common: DEFINE_*_TEST(x, ...)):
        # nombre -> (fichero, linea de la invocacion, funciones pasadas como argumento)
        self.macro_generated: Dict[str, Tuple[str, int, List[str], str]] = {}
        self.subtests: Dict[str, List[Tuple[str, int]]] = {}  # file -> [(fn, line)]
        self.stems: Dict[str, List[str]] = {}               # stem -> [files]
        self._text_cache: Dict[str, str] = {}
        self._build()

    # -- construccion --
    def _iter_files(self) -> Iterable[Path]:
        for path in self.repo_dir.rglob("*"):
            if path.suffix.lower() not in SRC_EXTS or not path.is_file():
                continue
            rel = path.relative_to(self.repo_dir).as_posix()
            if SKIP_DIRS_RE.search(rel):
                continue
            try:
                if path.stat().st_size > MAX_INDEX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield path

    def _build(self) -> None:
        # macro -> (params, piezas del nombre, fichero y linea del #define)
        templates: Dict[str, Tuple[List[str], List[str], str, int]] = {}
        texts: Dict[str, str] = {}
        for path in self._iter_files():
            rel = path.relative_to(self.repo_dir).as_posix()
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            is_test_file = False
            for m in _AWS_RE.finditer(text):
                args = [a.strip() for a in m.group(2).split(",")]
                if len(args) < 2 or not args[0].isidentifier():
                    continue
                is_test_file = True
                # con fixture, la funcion del test es el tercer argumento
                fn = args[2] if "FIXTURE" in m.group(1) and len(args) >= 3 else args[1]
                self.aws.setdefault(args[0], (rel, fn, _line_of(text, m.start())))
            for m in _CATCH_RE.finditer(text):
                is_test_file = True
                name = _unescape(m.group(2))
                self.catch.setdefault(name, (rel, _line_of(text, m.start())))
            for m in _GTEST_RE.finditer(text):
                is_test_file = True
                self.gtest.setdefault(f"{m.group(2)}.{m.group(3)}",
                                      (rel, _line_of(text, m.start())))
            for m in _MACRO_CASE_RE.finditer(text):
                if m.group(1) in ("TEST_CASE", "TEST", "TEST_F", "TEST_P", "UTEST"):
                    continue
                is_test_file = True
                self.macro_cases.setdefault(m.group(2), (rel, _line_of(text, m.start())))
            subs: List[Tuple[str, int]] = []
            seen = set()
            for regex in (_CMOCKA_RE, _NUTS_RE):
                for m in regex.finditer(text):
                    fn = m.group(1)
                    if fn not in seen:
                        seen.add(fn)
                        subs.append((fn, _line_of(text, m.start())))
            if subs:
                is_test_file = True
                self.subtests[rel] = subs
            if is_test_file or _TEST_FILE_HINT_RE.search(rel) or "main(" in text:
                self.stems.setdefault(path.stem.lower(), []).append(rel)
            if "##" in text and "define" in text:
                for d in _DEFINE_RE.finditer(text):
                    body = d.group(3)
                    a = _AWS_IN_DEFINE_RE.search(body)
                    if not a or "##" not in a.group(1):
                        continue
                    params = [x.strip() for x in d.group(2).split(",")]
                    pieces = [x.strip() for x in a.group(1).split("##")]
                    templates[d.group(1)] = (params, pieces, rel, _line_of(text, d.start()))
            texts[rel] = text
        self._index_macro_tests(templates, texts)

    def _index_macro_tests(self, templates, texts) -> None:
        if not templates:
            return
        call_re = re.compile(r"(?<![#\w])(" + "|".join(map(re.escape, templates)) + r")\s*\(")
        for rel, text in texts.items():
            for m in call_re.finditer(text):
                line_start = text.rfind("\n", 0, m.start()) + 1
                if text[line_start:m.start()].lstrip().startswith("#"):
                    continue   # la propia definicion
                depth, i = 1, m.end()
                while i < len(text) and depth:
                    depth += {"(": 1, ")": -1}.get(text[i], 0)
                    i += 1
                args = [x.strip() for x in text[m.end():i - 1].split(",")]
                params, pieces, def_rel, def_line = templates[m.group(1)]
                sub = dict(zip(params, args))
                name = "".join(sub.get(pc, pc) for pc in pieces)
                if not re.fullmatch(r"\w+", name):
                    continue
                # funciones pasadas como argumento (la logica del caso): en
                # el mismo fichero -> "fn"; en otro fichero -> "fichero|fn"
                funcs = []
                for a in args:
                    if not re.fullmatch(r"[A-Za-z_]\w*", a):
                        continue
                    fdef = re.compile(r"\b" + re.escape(a) + r"\s*\([^;{)]*\)\s*\{")
                    if fdef.search(text):
                        funcs.append(a)
                        continue
                    for other, otext in texts.items():
                        if other != rel and a in otext and fdef.search(otext):
                            funcs.append(f"{other}|{a}")
                            break
                self.macro_generated.setdefault(
                    name, (rel, _line_of(text, m.start()), funcs, f"{def_rel}:{def_line}"))

    # -- consulta --
    def text(self, rel: str) -> str:
        if rel not in self._text_cache:
            try:
                self._text_cache[rel] = (self.repo_dir / rel).read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                self._text_cache[rel] = ""
        return self._text_cache[rel]

    def function_line(self, rel: str, fn: str, default: int = 0) -> int:
        """Linea donde se define la funcion `fn` en `rel` (heuristica textual)."""
        text = self.text(rel)
        pat = re.compile(r"^[ \t]*(?:[\w:<>*&]+[ \t]+)*\**" + re.escape(fn) + r"[ \t]*\(", re.M)
        for m in pat.finditer(text):
            # descartar prototipos (terminan en ';' antes de abrir llave)
            tail = text[m.end(): m.end() + 400]
            close = tail.find(")")
            if close != -1 and "{" in tail[close:close + 200] and \
                    ";" not in tail[close:tail.find("{", close)]:
                return _line_of(text, m.start())
        return default

    def files_for_stem(self, stem: str) -> List[str]:
        stem = stem.lower()
        bases = [stem]
        for pre in ("utest_", "unittest_", "unit_", "test_", "tests_", "check_"):
            if stem.startswith(pre):
                bases.append(stem[len(pre):])
        for suf in ("_test", "-test", "_tests", "-tests", "test", "_unittest", "_utest"):
            if stem.endswith(suf) and len(stem) > len(suf):
                bases.append(stem[: -len(suf)])
        variants: List[str] = []
        for b in bases:
            for v in (b, "test_" + b, b + "_test", b + "-test", b + "_tests",
                      "utest_" + b, b + ".tests"):
                if v not in variants:
                    variants.append(v)
        found: List[str] = []
        for v in variants:
            for rel in self.stems.get(v, []):
                if rel not in found:
                    found.append(rel)
        # preferir ficheros bajo un directorio de tests y con macros de test
        found.sort(key=lambda r: (0 if _TEST_FILE_HINT_RE.search(r) else 1,
                                  0 if r in self.subtests else 1, len(r)))
        return found


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _unescape(s: str) -> str:
    return s.replace('\\"', '"').replace("\\\\", "\\")


# ───────────────────────────── resolucion ────────────────────────────────

def split_subtest(test_id: str) -> Tuple[str, str]:
    """'utest_binary/test_x' -> ('utest_binary', 'test_x'); sin sub -> (id, '')."""
    if SUBTEST_SEP in test_id:
        base, _, sub = test_id.partition(SUBTEST_SEP)
        return base, sub
    return test_id, ""


def resolve_test(test_id: str, ctest: Optional[CtestTest], index: TestIndex) -> TestSource:
    base, sub = split_subtest(test_id)
    ts = TestSource(test=test_id, command=ctest.command_str() if ctest else "")
    args = ctest.command[1:] if ctest else []
    sections = _section_args(args)
    candidates = _positional_args(args)

    # sub-test listado por el binario con su localizacion exacta
    if test_id in index.listed:
        rel, line, name = index.listed[test_id]
        ts.file, ts.line, ts.function = rel, line, name
        ts.granularity, ts.strategy = "function", "subtest"
        return ts

    # subtest expandido: la funcion vive en el fichero del ejecutable
    if sub:
        files = index.files_for_stem(ctest.exe_stem) if ctest else []
        if not files:
            files = index.files_for_stem(base)
        for rel in files:
            fns = dict(index.subtests.get(rel, []))
            if sub in fns or index.function_line(rel, sub):
                ts.file, ts.function = rel, sub
                ts.line = index.function_line(rel, sub, fns.get(sub, 0))
                ts.granularity, ts.strategy = "function", "subtest"
                return ts
        if "." in sub and sub in index.gtest:
            rel, line = index.gtest[sub]
            ts.file, ts.line, ts.function = rel, line, sub
            ts.granularity, ts.strategy = "function", "gtest_filter"
            return ts

    # aws-c-common: driver <nombre> con AWS_TEST_CASE(nombre, fn)
    for arg in candidates + [base]:
        if arg in index.aws:
            rel, fn, macro_line = index.aws[arg]
            ts.file, ts.function = rel, fn
            ts.line = index.function_line(rel, fn, macro_line)
            ts.granularity, ts.strategy = "function", "aws_test_case"
            return ts

    # test generado por una macro con ##: DEFINE_LOG_FORMATTER_TEST(fn, ...)
    for arg in candidates + [base]:
        if arg in index.macro_generated:
            rel, line, funcs, define_at = index.macro_generated[arg]
            ts.file, ts.line = rel, line
            ts.function = ",".join(funcs)
            ts.sections = define_at   # fichero:linea del #define (para expandirlo)
            ts.granularity, ts.strategy = "macro", "aws_test_macro"
            return ts

    # macro propia del proyecto con cuerpo: CBOR_TEST_CASE(nombre) { ... }
    for arg in candidates + [base]:
        if arg in index.macro_cases:
            rel, line = index.macro_cases[arg]
            ts.file, ts.line, ts.function = rel, line, arg
            ts.granularity, ts.strategy = "function", "test_macro"
            return ts

    # googletest: --gtest_filter=Suite.Name o nombre ctest "Suite.Name"
    gtest_names = [a.split("=", 1)[1] for a in args if a.startswith("--gtest_filter=")]
    gtest_names += [base]
    for name in gtest_names:
        key = _gtest_key(name)
        if key in index.gtest:
            rel, line = index.gtest[key]
            ts.file, ts.line, ts.function = rel, line, key
            ts.granularity, ts.strategy = "function", "gtest_filter"
            return ts

    # Catch2: un argumento (o el nombre) es el nombre del TEST_CASE; un
    # argumento puede ser una lista "a,b" (spec de Catch2) -> el primero que
    # exista. Con -c/--section el caso se recorta a esa seccion.
    for arg in candidates + [base]:
        for name in _catch_spec_names(arg):
            if name in index.catch:
                rel, line = index.catch[name]
                ts.file, ts.line, ts.function = rel, line, name
                ts.granularity, ts.strategy = "function", "catch_test_case"
                if sections:
                    ts.sections = " > ".join(sections)
                    ts.granularity = "section"
                return ts

    # script (python/bash ...) con un fichero del repo como argumento
    if ctest and Path(ctest.exe).stem.lower().rstrip("0123456789.") in SCRIPT_INTERPRETERS:
        for arg in candidates:
            rel = _relative_to_repo(arg, index.repo_dir)
            if rel:
                ts.file, ts.line = rel, 1
                ts.granularity, ts.strategy = "file", "script"
                return ts

    # un argumento nombra el objetivo real de un lanzador generico
    # (libyang: `fuzz_regression_test regress_fuzz_lyd_parse_mem_xml .` ->
    # tests/fuzz/lyd_parse_mem_xml.c): se prueba el argumento quitando
    # prefijos `a_`, `a_b_` antes que el propio ejecutable
    exe_stem = ctest.exe_stem.lower() if ctest else ""
    for arg in candidates:
        if not re.fullmatch(r"[A-Za-z_][\w\-]*", arg):
            continue
        parts = arg.split("_")
        for k in range(0, min(3, len(parts))):
            stem = "_".join(parts[k:])
            files = [f for f in index.files_for_stem(stem)
                     if Path(f).stem.lower() != exe_stem]
            if files:
                ts.file, ts.line = files[0], 1
                ts.granularity, ts.strategy = "file", "exe_stem"
                return ts

    # ejecutable con raiz comun con un fichero de tests
    stems = [ctest.exe_stem] if ctest else []
    stems.append(base)
    for stem in stems:
        files = index.files_for_stem(stem)
        if files:
            ts.file, ts.line = files[0], 1
            ts.granularity, ts.strategy = "file", "exe_stem"
            return ts

    # sin caso concreto (Catch2 --list-tests, -h, specs sin coincidencias...):
    # el test ES su definicion en CMake: add_test() + set_tests_properties()
    if ctest and ctest.def_file and ctest.def_line and not sub:
        rel = _relative_to_repo(ctest.def_file, index.repo_dir)
        if rel:
            ts.file, ts.line, ts.function = rel, ctest.def_line, ctest.name
            ts.granularity, ts.strategy = "ctest", "ctest_definition"
    return ts


_OPTS_WITH_VALUE = {"-c", "--section", "-r", "--reporter", "-o", "--out",
                    "-w", "--warn", "--order", "--rng-seed", "-d", "--durations",
                    "--verbosity", "--colour-mode", "--shard-count",
                    "--shard-index", "-x", "--abortx", "--benchmark-samples"}


def _positional_args(args: List[str]) -> List[str]:
    """Argumentos que no son opciones ni valores de opciones."""
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a in _OPTS_WITH_VALUE:
            skip = True
            continue
        if a and not a.startswith("-"):
            out.append(a)
    return out


def _section_args(args: List[str]) -> List[str]:
    out = []
    for i, a in enumerate(args):
        if a in ("-c", "--section") and i + 1 < len(args):
            out.append(args[i + 1])
    return out


def _catch_spec_names(arg: str) -> List[str]:
    """'Tracker,' -> ['Tracker']; '"a",b' -> ['a', 'b']; sin tags ni exclusiones."""
    names = []
    for part in re.split(r"(?<!\\),", arg):
        part = part.strip().strip('"').replace("\\,", ",")
        if part and not part.startswith(("[", "~", "exclude:")):
            names.append(part)
    return names or [arg]


def catch2_escape(name: str) -> str:
    """Spec de Catch2 que casa exactamente con `name`: entre comillas y con
    barra invertida delante de `\\`, `"` y `,`."""
    esc = name.replace("\\", "\\\\").replace('"', '\\"').replace(",", "\\,")
    return '"' + esc + '"'


def list_catch2_tests(exe: str, cwd: str, repo_dir: Path,
                      timeout: int = 120) -> List[Tuple[str, str, int]]:
    """[(nombre, fichero relativo al repo, linea)] de los TEST_CASE no ocultos
    del binario Catch2 (`--list-tests --verbosity high -r xml`)."""
    try:
        r = subprocess.run([exe, "--list-tests", "--verbosity", "high", "-r", "xml"],
                           cwd=cwd or None, capture_output=True, text=True,
                           timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_catch2_listing(r.stdout, repo_dir)


def parse_catch2_listing(xml_text: str, repo_dir: Path) -> List[Tuple[str, str, int]]:
    start = xml_text.find("<")
    if start == -1:
        return []
    try:
        root = ET.fromstring(xml_text[start:])
    except ET.ParseError:
        return []
    out = []
    prefixes = []
    for p in (Path(repo_dir).resolve().as_posix(), Path(repo_dir).as_posix()):
        p = p.rstrip("/") + "/"
        if p not in prefixes:
            prefixes.append(p)
    for tc in root.iter("TestCase"):
        name = (tc.findtext("Name") or "").strip()
        if not name:
            continue
        f = (tc.findtext("SourceInfo/File") or "").strip().replace("\\", "/")
        line = int((tc.findtext("SourceInfo/Line") or "0").strip() or 0)
        rel = f
        for prefix in prefixes:
            if f.startswith(prefix):
                rel = f[len(prefix):]
                break
        out.append((name, rel, line))
    return out


def direct_command(ctest: CtestTest, sub: str, mode: str, timeout_s: int,
                   build_dir: str = "", alt_build_dir: str = "") -> str:
    """Comando shell que ejecuta un sub-test llamando al binario sin ctest
    (Catch2: `SelfTest "<nombre>"`). `timeout` de coreutils: 124 = TIME.
    Con `alt_build_dir` el binario se toma del build de cobertura."""
    exe = ctest.exe
    cwd = ctest.working_dir or str(Path(exe).parent)
    if build_dir and alt_build_dir:
        b = build_dir.rstrip("/")
        exe = exe.replace(b, alt_build_dir.rstrip("/"), 1)
        cwd = cwd.replace(b, alt_build_dir.rstrip("/"), 1)
    spec = catch2_escape(sub) if mode == "catch2" else sub
    limit = max(1, int(timeout_s) - 2)
    return (f"cd {shlex.quote(cwd)} && exec timeout -k 5 {limit} "
            f"{shlex.quote(exe)} {shlex.quote(spec)}")


def prune_catch_sections(text: str, line: int, path: List[str]) -> Optional[List[str]]:
    """Lineas del TEST_CASE que contiene `line` quitando las SECTION hermanas
    que no estan en `path` (lo que ejecuta Catch2 con -c a -c b ...).
    Formato TestMethodCode: sin indentacion, sin la llave final."""
    from .source_extract import (_content_end_line, find_functions,
                                 mask_comments_and_strings)
    funcs = [f for f in find_functions(text) if f.header_line <= line <= f.close_brace_line]
    if not funcs:
        return None
    func = min(funcs, key=lambda f: f.close_brace_line - f.header_line)
    masked = mask_comments_and_strings(text)
    body_s, body_e = func.body_start_off, func.body_end_off
    blocks = []   # (inicio, fin, nombre) de cada SECTION(...) { ... }
    for m in re.finditer(r"\bSECTION\s*\(", masked[body_s:body_e]):
        s0 = body_s + m.start()
        after = body_s + m.end()
        name_m = re.match(r'\s*"((?:\\.|[^"\\])*)"', text[after:])
        depth, i = 1, after
        while i < body_e and depth:
            depth += {"(": 1, ")": -1}.get(masked[i], 0)
            i += 1
        j = masked.find("{", i, body_e)
        if j == -1 or masked[i:j].strip():
            continue
        depth, k = 1, j + 1
        while k < body_e and depth:
            depth += {"{": 1, "}": -1}.get(masked[k], 0)
            k += 1
        blocks.append((s0, k, _unescape(name_m.group(1)) if name_m else ""))
    removed = []
    for s0, e0, name in blocks:
        level = sum(1 for s1, e1, _ in blocks if s1 < s0 and e0 <= e1)
        if level < len(path) and name != path[level]:
            removed.append((s0, e0))
    chars = list(text)
    for s0, e0 in removed:
        for i in range(s0, e0):
            if chars[i] != "\n":
                chars[i] = "\0"
    pruned_lines = "".join(chars).splitlines()
    orig_lines = text.splitlines()
    end = _content_end_line(orig_lines, func)
    out = []
    for no in range(func.header_line, end + 1):
        pl = pruned_lines[no - 1]
        if "\0" in pl:
            kept = pl.replace("\0", "").strip()
            if kept:
                out.append(kept)
            continue
        out.append(orig_lines[no - 1].strip())
    return out


def _gtest_key(name: str) -> str:
    """'Prefix/Suite.Name/0' -> 'Suite.Name'; 'Suite.Name' -> igual."""
    core = name.split("/")[-2] if name.count("/") >= 2 else name
    if "/" in core:
        core = core.split("/")[-1]
    parts = core.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else core


def _relative_to_repo(arg: str, repo_dir: Path) -> str:
    try:
        p = Path(arg)
        if not p.is_absolute():
            p = repo_dir / p
        if p.is_file():
            return p.resolve().relative_to(repo_dir.resolve()).as_posix()
    except (OSError, ValueError):
        pass
    return ""


def map_tests(test_ids: Iterable[str], ctest_tests: Dict[str, CtestTest],
              index: TestIndex) -> List[TestSource]:
    out = []
    for tid in test_ids:
        base, _ = split_subtest(tid)
        out.append(resolve_test(tid, ctest_tests.get(base), index))
    return out


# ───────────────────────── expansion en sub-tests ─────────────────────────

def list_gtest_tests(exe: str, cwd: str = "", timeout: int = 60) -> List[str]:
    """['Suite.Name', ...] via `<exe> --gtest_list_tests` (vacio si no es gtest)."""
    try:
        r = subprocess.run([exe, "--gtest_list_tests"], cwd=cwd or None,
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    return parse_gtest_listing(r.stdout)


def parse_gtest_listing(text: str) -> List[str]:
    names: List[str] = []
    suite = ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip() or line.startswith("Running main()"):
            continue
        if not line.startswith(" "):
            suite = line.strip()
            continue
        if suite.endswith("."):
            names.append(suite + line.strip())
    return names


def list_cmocka_tests(index: TestIndex, ctest: Optional[CtestTest], name: str) -> List[str]:
    """Funciones registradas con UTEST()/cmocka_unit_test*() en el fichero
    del ejecutable de ese test (o con nombre coincidente con el test)."""
    files = index.files_for_stem(ctest.exe_stem) if ctest else []
    if not files:
        files = index.files_for_stem(name)
    for rel in files:
        subs = [fn for fn, _ in index.subtests.get(rel, [])]
        if subs:
            return subs
    return []


def expand_tests(test_ids: List[str], mode: str, ctest_tests: Dict[str, CtestTest],
                 index: TestIndex, timeout: int = 60,
                 only: Optional["re.Pattern"] = None) -> List[str]:
    """Sustituye cada test ctest por sus sub-tests `<test>/<sub>`:
      gtest   `<exe> --gtest_list_tests`; se ejecuta con GTEST_FILTER
      cmocka  funciones UTEST()/cmocka_unit_test*() del fichero del binario;
              se ejecuta con el shim LD_PRELOAD + CMOCKA_TEST_FILTER
      catch2  `<exe> --list-tests -r xml` (con fichero y linea); se ejecuta
              llamando al binario con el nombre del TEST_CASE
    `only` (regex) limita que tests ctest se expanden (Catch2: RunTests, que
    ejecuta la suite completa). Si no se puede listar, se deja tal cual."""
    out: List[str] = []
    for tid in test_ids:
        ct = ctest_tests.get(tid)
        subs: List[str] = []
        if only is not None and not only.search(tid):
            out.append(tid)
            continue
        if mode == "gtest" and ct and ct.exe and \
                not any(a.startswith("--gtest_filter") for a in ct.command):
            subs = list_gtest_tests(ct.exe, ct.working_dir, timeout)
        elif mode == "cmocka":
            subs = list_cmocka_tests(index, ct, tid)
        elif mode == "catch2" and ct and ct.exe:
            seen = set()
            for name, rel, line in list_catch2_tests(ct.exe, ct.working_dir,
                                                     index.repo_dir, timeout):
                if name.lower() in seen:   # Catch2 compara sin mayusculas
                    continue
                seen.add(name.lower())
                subs.append(name)
                index.listed[f"{tid}{SUBTEST_SEP}{name}"] = (rel, line, name)
        if subs:
            out.extend(f"{tid}{SUBTEST_SEP}{s}" for s in subs)
        else:
            out.append(tid)
    return out


def env_prefix_for(test_id: str, mode: str) -> str:
    """Prefijo `VAR=valor ` que filtra el sub-test dentro del ejecutable."""
    _, sub = split_subtest(test_id)
    if not sub or not mode:
        return ""
    if mode == "gtest":
        return f"GTEST_FILTER={shlex.quote(sub)} "
    if mode == "cmocka":
        return (f"LD_PRELOAD={shlex.quote(CMOCKA_FILTER_PRELOAD)} "
                f"CMOCKA_TEST_FILTER={shlex.quote(sub)} ")
    return ""


# ───────────────────────────── E/S CSV ───────────────────────────────────

def write_test_sources_csv(path: Path, sources: Dict[int, TestSource]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(TEST_SOURCES_COLUMNS)
        for test_no in sorted(sources):
            w.writerow(sources[test_no].row(test_no))


def read_test_sources_csv(path: Path) -> Dict[int, TestSource]:
    out: Dict[int, TestSource] = {}
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                no = int(row["TestNo"])
            except (KeyError, ValueError):
                continue
            out[no] = TestSource(test=row.get("TestName", ""),
                                 file=row.get("TestFile", ""),
                                 line=int(row.get("TestLine") or 0),
                                 function=row.get("TestFunction", ""),
                                 granularity=row.get("Granularity", "none"),
                                 strategy=row.get("Strategy", "none"),
                                 command=row.get("Command", ""),
                                 sections=row.get("Sections", "") or "")
    return out


def source_url(repo_url: str, commit: str, rel_file: str, line: int = 0) -> str:
    """Enlace permanente al fichero en el commit (GitHub/GitLab)."""
    if not repo_url or not commit or not rel_file:
        return ""
    base = repo_url.rstrip("/")
    if base.endswith(".git"):
        base = base[:-4]
    sep = "/-/blob/" if "gitlab" in base else "/blob/"
    url = f"{base}{sep}{commit}/{rel_file}"
    return f"{url}#L{line}" if line else url


# ───────────────────────────── CLI ───────────────────────────────────────

def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Enlace test de ctest -> codigo fuente del caso de prueba")
    parser.add_argument("--repo", required=True, help="raiz del repo del proyecto")
    parser.add_argument("--build-dir", help="build CMake (para ctest --show-only)")
    parser.add_argument("--ctest-json", help="json-v1 ya volcado (alternativa a --build-dir)")
    parser.add_argument("--tests-from", help="testMap.csv del raw: mapea solo esos tests")
    parser.add_argument("--out", required=True, help="directorio donde escribir test_sources.csv")
    parser.add_argument("--expansion", default="",
                        help="modo de expansion usado en la mutacion (catch2/gtest/cmocka): "
                             "se vuelven a listar los casos para enlazarlos")
    parser.add_argument("--expand-only", default="",
                        help="regex de tests ctest que se expandieron")
    parser.add_argument("--relink", action="store_true",
                        help="conservar los sub-tests ya enlazados de un test_sources.csv previo")
    args = parser.parse_args(argv)

    if args.ctest_json:
        ctest_tests = parse_ctest_json(Path(args.ctest_json).read_text(encoding="utf-8"))
    elif args.build_dir:
        ctest_tests, _ = load_ctest_tests(Path(args.build_dir))
    else:
        parser.error("hace falta --build-dir o --ctest-json")
    index = TestIndex(Path(args.repo))

    if args.tests_from:
        from .major_format import read_test_map, split_test_name
        raw_map = read_test_map(Path(args.tests_from))
        ids = {no: split_test_name(name)[1] or name for no, name in raw_map.items()}
    else:
        ids = {i + 1: name for i, name in enumerate(sorted(ctest_tests))}

    previous: Dict[int, TestSource] = {}
    if args.relink:
        # re-resolver un raw ya generado: los sub-tests enlazados con la
        # localizacion que dio el propio binario (Catch2 --list-tests) se
        # conservan; el resto se vuelve a resolver con el indice actual
        prev_path = Path(args.out) / "test_sources.csv"
        if prev_path.is_file():
            previous = read_test_sources_csv(prev_path)
    if args.expansion:
        bases = sorted({split_subtest(i)[0] for i in ids.values()
                        if split_subtest(i)[1]} & set(ctest_tests))
        expand_tests(bases, args.expansion, ctest_tests, index,
                     only=re.compile(args.expand_only) if args.expand_only else None)
    mapped = map_tests([ids[k] for k in sorted(ids)], ctest_tests, index)
    sources = {no: ts for no, ts in zip(sorted(ids), mapped)}
    for no, old in previous.items():
        new = sources.get(no)
        if new is not None and GRANULARITY_RANK.get(old.granularity, 0) >                 GRANULARITY_RANK.get(new.granularity, 0):
            sources[no] = old     # nunca empeorar un enlace previo
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_test_sources_csv(out_dir / "test_sources.csv", sources)
    from collections import Counter
    strat = Counter(ts.strategy for ts in mapped)
    logger.info("%d tests mapeados -> %s  (%s)", len(mapped),
                out_dir / "test_sources.csv", dict(strat))
    return 0


if __name__ == "__main__":
    sys.exit(main())
