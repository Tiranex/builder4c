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

    results = build_results_rows(raw_dir, src_resolver,
                                 keep_missing_source=args.keep_missing_source)
    test_map = build_test_map_rows(raw_dir, test_resolver,
                                   keep_missing_source=args.keep_missing_source)

    prefix = f"{args.project}_{args.version}"
    results_path = out_dir / f"{prefix}_results.csv"
    test_map_path = out_dir / f"{prefix}_test_map.csv"
    write_csv(results_path, RESULT_COLUMNS, results)
    write_csv(test_map_path, TEST_MAP_COLUMNS, test_map)

    logger.info("%s: %d filas de mutantes", results_path, len(results))
    logger.info("%s: %d filas de tests", test_map_path, len(test_map))
    return 0


if __name__ == "__main__":
    sys.exit(main())
