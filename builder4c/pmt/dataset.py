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


def build_test_map_rows(raw_dir: Path,
                        test_src_resolver: Optional[SourceResolver],
                        keep_missing_source: bool = False) -> List[List]:
    test_names = read_test_map(raw_dir / "testMap.csv")
    source_cache: Dict[str, Optional[str]] = {}
    rows: List[List] = []
    skipped = 0
    for test_no in sorted(test_names):
        raw_name = test_names[test_no]
        cls, method = split_test_name(raw_name)
        text = None
        if test_src_resolver is not None:
            if cls not in source_cache:
                source_cache[cls] = test_src_resolver(cls)
            text = source_cache[cls]
        extracted = extract_named_method(text, method) if (text and method) else None
        if extracted is None:
            skipped += 1
            if not keep_missing_source:
                continue
            code = "[]"
        else:
            code = str(extracted.lines)
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
