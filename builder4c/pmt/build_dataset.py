"""CLI: construye el dataset final PMT desde los ficheros en bruto.

Uso (desde builder4c/):

  # C++ (clases derivadas de rutas: src.calculator -> <root>/src/calculator.cpp)
  python3 -m pmt.build_dataset --input demo/out/Demo_1/raw --project Demo \
      --version 1 --language cpp --src-root demo/demo___mathlib --out demo/out

  # Java (clases por paquete: org.x.Y -> <src-root>/org/x/Y.java)
  python3 -m pmt.build_dataset --input Csv_1_fixed --project Csv --version 1 \
      --language java --src-root repo/src/main/java --test-src-root repo/src/test/java

Sin --src-root, las columnas de codigo quedan vacias y hay que pasar
--keep-missing-source para no descartar las filas.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

from .dataset import (
    RESULT_COLUMNS,
    TEST_MAP_COLUMNS,
    build_results_rows,
    build_test_map_rows,
    write_csv,
)

TEST_SOURCES_SIDECAR_COLUMNS = ["TestMethod", "TestFile", "TestLine", "TestFunction",
                                "Granularity", "Sections", "Strategy", "Command",
                                "SourceUrl"]

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CPP_EXTENSIONS = [".cpp", ".cc", ".cxx", ".c", ".hpp", ".hh", ".h"]


def make_resolver(roots: List[Path], language: str):
    """Devuelve un resolver nombre-de-clase -> contenido del fichero fuente."""
    if not roots:
        return None

    def resolve(class_name: str) -> Optional[str]:
        # las clases internas Java (Outer$Inner) viven en el fichero de Outer
        base = class_name.split("$")[0].replace(".", "/")
        candidates = []
        if language == "java":
            candidates.append(base + ".java")
        else:
            candidates.extend(base + ext for ext in CPP_EXTENSIONS)
        for root in roots:
            for rel in candidates:
                path = root / rel
                if path.is_file():
                    return path.read_text(encoding="utf-8", errors="replace")
        logger.debug("sin fichero fuente para %s", class_name)
        return None

    return resolve


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Genera [proyecto]_[version]_results.csv y _test_map.csv "
                    "con el mismo esquema que el dataset PMT de Java")
    parser.add_argument("--input", required=True,
                        help="directorio con mutants.log/testMap/covMap/killMap")
    parser.add_argument("--project", required=True, help="nombre del proyecto (ej. Csv)")
    parser.add_argument("--version", required=True, help="version del proyecto (ej. 1)")
    parser.add_argument("--out", required=True, help="directorio de salida")
    parser.add_argument("--language", choices=["java", "cpp"], default="cpp")
    parser.add_argument("--src-root", action="append", default=[],
                        help="raiz del codigo fuente (repetible)")
    parser.add_argument("--test-src-root", action="append", default=[],
                        help="raiz del codigo de tests (por defecto, los src-root)")
    parser.add_argument("--keep-missing-source", action="store_true",
                        help="mantener filas aunque no se pueda extraer el codigo")
    args = parser.parse_args(argv)

    raw_dir = Path(args.input)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    src_roots = [Path(p) for p in args.src_root]
    test_roots = [Path(p) for p in args.test_src_root] or src_roots

    src_resolver = make_resolver(src_roots, args.language)
    test_resolver = make_resolver(test_roots, args.language)

    # el primer src-root es la raiz del repo: ahi se resuelven las rutas de
    # test_sources.csv (enlace test ctest -> fichero/funcion del caso de prueba)
    repo_root = src_roots[0] if src_roots else None
    results = build_results_rows(raw_dir, src_resolver,
                                 keep_missing_source=args.keep_missing_source)
    test_map = build_test_map_rows(raw_dir, test_resolver,
                                   keep_missing_source=args.keep_missing_source,
                                   repo_root=repo_root)

    prefix = f"{args.project}_{args.version}"
    results_path = out_dir / f"{prefix}_results.csv"
    test_map_path = out_dir / f"{prefix}_test_map.csv"
    write_csv(results_path, RESULT_COLUMNS, results)
    write_csv(test_map_path, TEST_MAP_COLUMNS, test_map)

    logger.info("%s: %d filas de mutantes", results_path, len(results))
    logger.info("%s: %d filas de tests", test_map_path, len(test_map))
    write_test_sources_sidecar(raw_dir, out_dir, prefix)
    return 0


def write_test_sources_sidecar(raw_dir: Path, out_dir: Path, prefix: str) -> None:
    """<prefix>_test_sources.csv: para cada TestMethod del test_map, fichero,
    linea, funcion, granularidad, estrategia de enlace, comando ctest y URL
    permanente al commit (si meta.json trae repo_url y commit). Ademas copia
    meta.json (commit, fuentes mutadas, limites) junto a los CSV."""
    ts_path = raw_dir / "test_sources.csv"
    meta: dict = {}
    meta_path = raw_dir / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
        (out_dir / f"{prefix}_meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not ts_path.is_file():
        return
    from .major_format import dotted_test_name, read_test_map
    from .test_source_map import read_test_sources_csv, source_url
    test_names = read_test_map(raw_dir / "testMap.csv")
    sources = read_test_sources_csv(ts_path)
    rows = []
    for test_no in sorted(test_names):
        ts = sources.get(test_no)
        if ts is None:
            continue
        rows.append([dotted_test_name(test_names[test_no]), ts.file, ts.line,
                     ts.function, ts.granularity,
                     ts.sections if ts.granularity == "section" else "",
                     ts.strategy, ts.command,
                     source_url(meta.get("repo_url", ""), meta.get("commit", ""),
                                ts.file, ts.line)])
    path = out_dir / f"{prefix}_test_sources.csv"
    write_csv(path, TEST_SOURCES_SIDECAR_COLUMNS, rows)
    linked = sum(1 for r in rows if r[1])
    logger.info("%s: %d/%d tests con enlace a su codigo", path, linked, len(rows))


if __name__ == "__main__":
    sys.exit(main())
