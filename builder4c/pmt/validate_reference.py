"""Valida el pipeline contra el dataset Java de referencia (Csv_1).

Genera las filas desde los ficheros en bruto y las compara con el dataset
publicado columna a columna. Las columnas de codigo fuente (SrcLines, Body,
SrcMethodKey, MutSrcLineNo) solo se comparan si se dispone del codigo Java
(--src-root); sin el, se validan el resto: identificacion del mutante,
estado, listas de tests y tokenizacion PMT.

Uso (desde builder4c/):
  python3 -m pmt.validate_reference \
      --raw ../Csv_1_fixed_mutations/Csv_1_fixed \
      --ref-results ../Csv_1_results/Csv_1_results.csv \
      --ref-test-map ../Csv_1_results/Csv_1_test_map.csv
"""

import argparse
import ast
import csv
import sys
from pathlib import Path

from .dataset import RESULT_COLUMNS, build_results_rows, build_test_map_rows

# columnas comparables sin necesidad del codigo fuente Java
COMPARABLE = ["Class", "Method", "Line", "Status", "Label", "Operator",
              "Before", "After", "BeforePMT", "AfterPMT", "MatchingIdx"]
# listas de tests: los conjuntos deben coincidir; el orden interno de la
# referencia es un artefacto de ejecucion de Major y solo se informa
LIST_COLUMNS = ["Tests", "KillingTests", "PassingTests"]
SOURCE_COLUMNS = ["SrcMethodKey", "SrcLines", "MutSrcLineNo", "Body"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", required=True)
    parser.add_argument("--ref-results", required=True)
    parser.add_argument("--ref-test-map", required=True)
    parser.add_argument("--src-root", action="append", default=[])
    args = parser.parse_args(argv)

    csv.field_size_limit(10 ** 9)

    resolver = None
    if args.src_root:
        from .build_dataset import make_resolver
        resolver = make_resolver([Path(p) for p in args.src_root], "java")

    ours = build_results_rows(Path(args.raw), resolver, keep_missing_source=True)
    ours_by_count = {row[RESULT_COLUMNS.index("Count")]: dict(zip(RESULT_COLUMNS, row))
                     for row in ours}

    with open(args.ref_results, encoding="utf-8", errors="replace", newline="") as fh:
        ref_rows = {int(r["Count"]): r for r in csv.DictReader(fh)}

    missing = sorted(set(ref_rows) - set(ours_by_count))
    print(f"filas generadas: {len(ours_by_count)}  filas referencia: {len(ref_rows)}")
    print(f"mutantes de la referencia ausentes en lo generado: {len(missing)}")

    compare_cols = COMPARABLE + (SOURCE_COLUMNS if args.src_root else [])
    mismatches = {c: 0 for c in compare_cols}
    set_mismatches = {c: 0 for c in LIST_COLUMNS}
    order_only = {c: 0 for c in LIST_COLUMNS}
    examples = {}
    for count, ref in ref_rows.items():
        mine = ours_by_count.get(count)
        if mine is None:
            continue
        for col in compare_cols:
            if str(mine[col]) != str(ref[col]):
                mismatches[col] += 1
                examples.setdefault(col, (count, str(mine[col])[:80], str(ref[col])[:80]))
        for col in LIST_COLUMNS:
            mine_list = ast.literal_eval(str(mine[col]))
            ref_list = ast.literal_eval(ref[col])
            if set(mine_list) != set(ref_list):
                set_mismatches[col] += 1
                examples.setdefault(col, (count, str(mine[col])[:80], ref[col][:80]))
            elif mine_list != ref_list:
                order_only[col] += 1

    ok = not missing
    print("\ncomparacion por columna (filas coincidentes):")
    for col in compare_cols:
        n = mismatches[col]
        flag = "OK " if n == 0 else "FAIL"
        if n:
            ok = False
        print(f"  {flag} {col:14s} diferencias: {n}")
        if col in examples and n:
            c, m, r = examples[col]
            print(f"        ej. Count={c}\n        nuestro:    {m}\n        referencia: {r}")
    for col in LIST_COLUMNS:
        n = set_mismatches[col]
        flag = "OK " if n == 0 else "FAIL"
        if n:
            ok = False
        print(f"  {flag} {col:14s} conjuntos distintos: {n} "
              f"(solo orden interno: {order_only[col]})")
        if col in examples and n:
            c, m, r = examples[col]
            print(f"        ej. Count={c}\n        nuestro:    {m}\n        referencia: {r}")

    # test_map: los nombres del dataset deben ser un superconjunto consistente
    tm_ours = {row[0] for row in build_test_map_rows(Path(args.raw), None,
                                                     keep_missing_source=True)}
    with open(args.ref_test_map, encoding="utf-8", errors="replace", newline="") as fh:
        tm_ref = {r["TestMethod"] for r in csv.DictReader(fh)}
    extra_ref = tm_ref - tm_ours
    print(f"\ntest_map: nuestros={len(tm_ours)} referencia={len(tm_ref)} "
          f"referencia-no-en-nuestros={len(extra_ref)}")
    if extra_ref:
        ok = False
        print("  faltan:", sorted(extra_ref)[:5])

    print("\nRESULTADO:", "OK" if ok else "CON DIFERENCIAS")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
