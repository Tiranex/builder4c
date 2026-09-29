"""Construccion del dataset final PMT a partir de los ficheros en bruto.

Esquema identico al dataset Java de referencia (Csv_1_results):

[proyecto]_results.csv  - una fila por mutante cubierto con metodo extraible:
    Class,Count,Method,Line,SrcMethodKey,Status,Label,Tests,KillingTests,
    PassingTests,Operator,SrcLines,MutSrcLineNo,Before,After,BeforePMT,
    AfterPMT,Body,MatchingIdx

[proyecto]_test_map.csv - una fila por caso de prueba:
    TestMethod,TestMethodCode

Reglas replicadas del pipeline Java:
  - Status: KILLED si algun test lo mata (killMap), SURVIVED si esta cubierto
    (covMap) pero nadie lo mata. Los mutantes no cubiertos no generan fila.
  - Label: 1 (KILLED) / 0 (SURVIVED).
  - KillingTests: tests que lo matan (killMap). PassingTests: tests que lo
    cubren y no lo matan. Tests = KillingTests + PassingTests (concatenacion,
    regla verificada contra la referencia). Ambas listas se ordenan por
    TestNo; en la referencia el orden interno de PassingTests refleja el
    orden de ejecucion por mutante de Major (no derivable del raw) y puede
    diferir, pero los conjuntos son identicos.
  - STD: la columna After usa "NOOP" y AfterPMT "<noop>," (como la referencia).
  - SrcMethodKey: {Clase}_{lineaInicioMetodo}_{lineaFinContenido}.
  - SrcLines: lineas del metodo sin indentar, sin la llave de cierre (repr lista).
  - MutSrcLineNo: Line - lineaInicioMetodo (indice relativo dentro del metodo).
  - Body: texto (strip) de la linea mutada.
  - BeforePMT/AfterPMT: tokenizacion de Before/After ("tok1, tok2,").
  - MatchingIdx: vacio (asi aparece en el dataset de referencia).
  - Si no se puede extraer el codigo del metodo/test, la fila se descarta
    (comportamiento observado en la referencia) salvo keep_missing_source.
"""

import csv
import logging
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .major_format import (
    dotted_test_name,
    read_cov_map,
    read_kill_map,
    read_mutants_log,
    read_test_map,
    split_test_name,
)
from .source_extract import extract_enclosing_method, extract_named_method, pmt_tokens

logger = logging.getLogger(__name__)

RESULT_COLUMNS = [
    "Class", "Count", "Method", "Line", "SrcMethodKey", "Status", "Label",
    "Tests", "KillingTests", "PassingTests", "Operator", "SrcLines",
    "MutSrcLineNo", "Before", "After", "BeforePMT", "AfterPMT", "Body",
    "MatchingIdx",
]

TEST_MAP_COLUMNS = ["TestMethod", "TestMethodCode"]

# Resolver: nombre de clase -> texto del fichero fuente (o None si no existe)
SourceResolver = Callable[[str], Optional[str]]


def build_results_rows(raw_dir: Path,
                       src_resolver: Optional[SourceResolver],
                       keep_missing_source: bool = False) -> List[List]:
    mutants = read_mutants_log(raw_dir / "mutants.log")
    test_names = read_test_map(raw_dir / "testMap.csv")
    cov_pairs = read_cov_map(raw_dir / "covMap.csv")
    kill_triples = read_kill_map(raw_dir / "killMap.csv")

    cov_by_mutant: Dict[int, List[int]] = defaultdict(list)
    for test_no, mutant_no in cov_pairs:
        cov_by_mutant[mutant_no].append(test_no)
    kill_by_mutant: Dict[int, List[int]] = defaultdict(list)
    for test_no, mutant_no, _reason in kill_triples:
        kill_by_mutant[mutant_no].append(test_no)

    source_cache: Dict[str, Optional[str]] = {}

    def resolve(class_name: str) -> Optional[str]:
        if src_resolver is None:
            return None
        if class_name not in source_cache:
            source_cache[class_name] = src_resolver(class_name)
        return source_cache[class_name]

    rows: List[List] = []
    skipped_no_source = 0
    for mutant_no in sorted(mutants):
        rec = mutants[mutant_no]
        if rec.method is None:
            continue  # mutantes fuera de un metodo no generan fila
        covering = sorted(set(cov_by_mutant.get(mutant_no, [])))
        if not covering:
            continue  # mutante no cubierto: sin fila (igual que la referencia)
        killing = sorted(set(kill_by_mutant.get(mutant_no, [])))
        killing = [t for t in killing if t in set(covering)] or killing
        passing = [t for t in covering if t not in set(killing)]

        text = resolve(rec.class_name)
        extracted = extract_enclosing_method(text, rec.line) if text else None
        if extracted is None:
            skipped_no_source += 1
            if not keep_missing_source:
                continue

        if extracted is not None:
            src_key = f"{rec.class_name}_{extracted.start_line}_{extracted.end_line}"
            src_lines = str(extracted.lines)
            mut_src_line_no = rec.line - extracted.start_line
            body = _line_text(text, rec.line)
        else:
            src_key, src_lines, mut_src_line_no, body = "", "[]", "", ""

        def names(test_nos: List[int]) -> List[str]:
            return [dotted_test_name(test_names[t]) for t in test_nos
                    if t in test_names]

        killing_names = names(killing)
        passing_names = names(passing)

        # caso especial STD, igual que en la referencia: <NO-OP> -> NOOP/<noop>,
        if rec.after == "<NO-OP>":
            after, after_pmt = "NOOP", "<noop>,"
        else:
            after, after_pmt = rec.after, pmt_tokens(rec.after)

        rows.append([
            rec.class_name,
            mutant_no,
            rec.method,
            rec.line,
            src_key,
            "KILLED" if killing else "SURVIVED",
            1 if killing else 0,
            str(killing_names + passing_names),  # Tests = Killing + Passing
            str(killing_names),
            str(passing_names),
            rec.operator,
            src_lines,
            mut_src_line_no,
            rec.before,
            after,
            pmt_tokens(rec.before),
            after_pmt,
            body,
            "",  # MatchingIdx: siempre vacio en la referencia
        ])

    if skipped_no_source:
        action = "mantenidas sin codigo" if keep_missing_source else "descartadas"
        logger.warning("%d filas sin codigo fuente extraible (%s)",
                       skipped_no_source, action)
    return rows


def test_code_from_source(repo_root: Path, ts) -> Optional[List[str]]:
    """Lineas (sin indentacion, sin llave final) del caso de prueba descrito
    por una fila de test_sources.csv (pmt.test_source_map.TestSource):
    granularidad `function` -> la funcion/bloque que contiene la linea;
    `file` -> el fichero entero. None si no hay fichero o no se puede leer."""
    if not ts.file:
        return None
    path = repo_root / ts.file
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    if ts.granularity == "section" and ts.line:
        # Catch2 -c: solo la(s) SECTION que ejecuta ese test
        from .test_source_map import prune_catch_sections
        path_names = [p.strip() for p in (ts.sections or "").split(" > ") if p.strip()]
        lines = prune_catch_sections(text, ts.line, path_names)
        if lines:
            return lines
    if ts.granularity == "macro" and ts.line:
        # test generado por macro: la invocacion + las funciones que recibe
        text_lines = text.splitlines()
        out: List[str] = []
        depth, started = 0, False
        for raw in text_lines[ts.line - 1:]:
            out.append(raw.strip())
            depth += raw.count("(") - raw.count(")")
            started = started or "(" in raw
            if started and depth <= 0:
                break
        # cuerpo del #define con los argumentos de esta invocacion sustituidos
        # (es el codigo real del test generado)
        invocation = " ".join(out)
        define_rel, _, define_line = (ts.sections or "").rpartition(":")
        if define_rel and define_line.isdigit():
            out.extend(_expand_macro(repo_root / define_rel, int(define_line), invocation))
        for fn in [f for f in (ts.function or "").split(",") if f]:
            other, _, name = fn.rpartition("|")
            ftext = text
            if other:
                fpath = repo_root / other
                if not fpath.is_file():
                    continue
                ftext = fpath.read_text(encoding="utf-8", errors="replace")
            extracted = extract_named_method(ftext, name)
            if extracted is not None:
                out.extend(extracted.lines)
        return out or None
    if ts.granularity == "ctest" and ts.line:
        # test definido solo en CMake: add_test(...) + set_tests_properties
        # del mismo nombre, mas el comando ya resuelto por ctest
        return _cmake_test_definition(text, ts.line, ts.function) + (
            [f"# ctest: {ts.command}"] if ts.command else [])
    if ts.granularity in ("function", "section"):
        extracted = None
        if ts.line:
            extracted = extract_enclosing_method(text, ts.line)
        if extracted is None and ts.function:
            extracted = extract_named_method(text, ts.function)
        if extracted is not None:
            return extracted.lines
        if ts.line:
            # caso generado por una macro de una linea (Catch2:
            # ADD_TRAIT_TEST_CASE(lt), METHOD_AS_TEST_CASE(Clase::metodo, ...))
            macro_code = _macro_invocation_code(text, ts.line)
            if macro_code:
                return macro_code
        return None
    if ts.granularity == "file":
        return [l.strip() for l in text.splitlines()]
    return None


def _balanced_block(lines: List[str], start: int) -> List[str]:
    """Lineas (strip) desde `start` (0-based) hasta cerrar el parentesis."""
    out, depth, opened = [], 0, False
    for raw in lines[start:]:
        out.append(raw.strip())
        depth += raw.count("(") - raw.count(")")
        opened = opened or "(" in raw
        if opened and depth <= 0:
            break
    return out


def _cmake_test_definition(text: str, line: int, name: str) -> List[str]:
    """Bloque add_test(...) de la linea `line` y los set_tests_properties
    posteriores que nombran el mismo test (hasta el siguiente add_test)."""
    import re
    lines = text.splitlines()
    if not (1 <= line <= len(lines)):
        return []
    out = _balanced_block(lines, line - 1)
    i = line - 1 + len(out)
    name_re = re.compile(r"set_tests_properties\s*\(\s*\"?" + re.escape(name) + r"\"?[\s)]")
    tmpl_re = re.compile(r"set_tests_properties\s*\(")
    while i < len(lines):
        raw = lines[i]
        if re.match(r"\s*add_test\s*\(", raw):
            break
        if name_re.search(raw) or (tmpl_re.search(raw) and "${" in raw):
            block = _balanced_block(lines, i)
            out.extend(block)
            i += len(block)
            continue
        i += 1
    return [l for l in out if l]


def _macro_invocation_code(text: str, line: int) -> Optional[List[str]]:
    """Invocacion MACRO(args) de la linea `line` + expansion del #define del
    mismo fichero, si existe, + metodos `Clase::metodo` referenciados."""
    import re
    lines = text.splitlines()
    if not (1 <= line <= len(lines)):
        return None
    invocation = _balanced_block(lines, line - 1)
    m = re.match(r"\s*(\w+)\s*\(", invocation[0] if invocation else "")
    if not m:
        return None
    out = list(invocation)
    d = re.search(r"^[ \t]*#[ \t]*define[ \t]+" + re.escape(m.group(1)) + r"\(", text, re.M)
    if d:
        def_line = text.count("\n", 0, d.start()) + 1
        out.extend(_expand_macro_text(text, def_line, " ".join(invocation)))
    for ref in re.findall(r"\b\w+::(\w+)\b", " ".join(invocation)):
        extracted = extract_named_method(text, ref)
        if extracted is not None:
            out.extend(extracted.lines)
    return out if len(out) > len(invocation) or d else None


def _expand_macro_text(text: str, define_line: int, invocation: str) -> List[str]:
    import re
    lines = text.splitlines()
    block = []
    for raw in lines[define_line - 1:]:
        block.append(raw.rstrip())
        if not raw.rstrip().endswith("\\"):
            break
    joined = "\n".join(l[:-1] if l.endswith("\\") else l for l in block)
    m = re.match(r"\s*#\s*define\s+\w+\(([^)]*)\)(.*)", joined, re.S)
    a = re.search(r"\((.*)\)", invocation, re.S)
    if not m or not a:
        return []
    params = [x.strip() for x in m.group(1).split(",")]
    args, depth, cur = [], 0, ""
    for ch in a.group(1):
        if ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
            continue
        depth += {"(": 1, ")": -1}.get(ch, 0)
        cur += ch
    args.append(cur.strip())
    body = m.group(2)
    for p, v in zip(params, args):
        if p:
            body = re.sub(r"\b" + re.escape(p) + r"\b", lambda _m, v=v: v, body)
    body = re.sub(r"\s*##\s*", "", body)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    return [l.strip() for l in body.splitlines() if l.strip()]


def _expand_macro(define_file: Path, define_line: int, invocation: str) -> List[str]:
    """Lineas del cuerpo de `#define M(p1, p2) ...` (en `define_line` de
    `define_file`) con los parametros sustituidos por los argumentos de
    `invocation` y `##` resuelto; sin indentacion ni lineas vacias."""
    if not define_file.is_file():
        return []
    return _expand_macro_text(define_file.read_text(encoding="utf-8", errors="replace"),
                              define_line, invocation)


def build_test_map_rows(raw_dir: Path,
                        test_src_resolver: Optional[SourceResolver],
                        keep_missing_source: bool = False,
                        repo_root: Optional[Path] = None) -> List[List]:
    """Filas TestMethod,TestMethodCode. El codigo sale, por este orden, del
    enlace test->fuente de `test_sources.csv` (proyectos ctest, si existe y
    se da `repo_root`) o del resolver clasico Clase[metodo] (demo/Java)."""
    test_names = read_test_map(raw_dir / "testMap.csv")
    test_sources = {}
    ts_path = raw_dir / "test_sources.csv"
    if repo_root is not None and ts_path.is_file():
        from .test_source_map import read_test_sources_csv
        test_sources = read_test_sources_csv(ts_path)
    source_cache: Dict[str, Optional[str]] = {}
    rows: List[List] = []
    skipped = 0
    for test_no in sorted(test_names):
        raw_name = test_names[test_no]
        cls, method = split_test_name(raw_name)
        extracted = None
        lines: Optional[List[str]] = None
        if test_no in test_sources and repo_root is not None:
            lines = test_code_from_source(repo_root, test_sources[test_no])
        if lines is None:
            text = None
            if test_src_resolver is not None:
                if cls not in source_cache:
                    source_cache[cls] = test_src_resolver(cls)
                text = source_cache[cls]
            extracted = extract_named_method(text, method) if (text and method) else None
            if extracted is not None:
                lines = extracted.lines
        if lines is None:
            skipped += 1
            if not keep_missing_source:
                continue
            code = "[]"
        else:
            code = str(lines)
        rows.append([dotted_test_name(raw_name), code])
    if skipped:
        action = "mantenidos sin codigo" if keep_missing_source else "descartados"
        logger.warning("%d tests sin codigo fuente extraible (%s)", skipped, action)
    return rows


def write_csv(path: Path, header: List[str], rows: List[List]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _line_text(text: str, line_no: int) -> str:
    lines = text.splitlines()
    if 1 <= line_no <= len(lines):
        return lines[line_no - 1].strip()
    return ""
