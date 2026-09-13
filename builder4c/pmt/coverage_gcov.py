"""Matriz de cobertura test <-> linea usando gcov (gcc --coverage).

Para cada test se ejecuta el binario instrumentado tras borrar los .gcda,
se invoca gcov sobre cada fichero fuente y se parsea el .gcov de texto
(`<contador>: <linea>: <codigo>`), quedandonos con las lineas ejecutadas.

Es el equivalente de la condicion "el test alcanza al mutante" de covMap.csv.
"""

import glob
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Set

logger = logging.getLogger(__name__)

_GCOV_LINE_RE = re.compile(r"^\s*([0-9]+[*]?|#####|=====|-):\s*(\d+):")


def _executed_lines_from_gcov(gcov_path: Path) -> Set[int]:
    executed = set()
    with open(gcov_path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            m = _GCOV_LINE_RE.match(raw)
            if not m:
                continue
            count, line_no = m.group(1), int(m.group(2))
            if count not in ("-", "#####", "====="):
                executed.add(line_no)
    return executed


def collect_coverage(root: Path,
                     sources: List[str],
                     object_dir: str,
                     run_test_cmd: str,
                     tests: List[str],
                     timeout_s: int) -> Dict[str, Dict[str, Set[int]]]:
    """Devuelve {test: {fichero_fuente_relativo: lineas_ejecutadas}}."""
    coverage: Dict[str, Dict[str, Set[int]]] = {}
    obj_dir = root / object_dir

    for test in tests:
        for gcda in glob.glob(str(obj_dir / "**" / "*.gcda"), recursive=True):
            os.remove(gcda)

        cmd = run_test_cmd.format(test=test)
        try:
            subprocess.run(cmd.split(), cwd=root, timeout=timeout_s,
                           capture_output=True)
        except subprocess.TimeoutExpired:
            logger.warning("timeout ejecutando %s durante la cobertura", test)

        # gcov resuelve la ruta del fuente tal y como se registro al compilar
        # (relativa a la raiz del proyecto), asi que se ejecuta desde root y
        # se limpian los .gcov generados despues de parsearlos
        per_file: Dict[str, Set[int]] = {}
        for src in sources:
            result = subprocess.run(
                ["gcov", "-o", str(obj_dir), src],
                cwd=root, capture_output=True, text=True)
            if result.returncode != 0:
                logger.warning("gcov fallo para %s: %s", src,
                               result.stderr.strip()[:200])
                continue
            gcov_file = root / (Path(src).name + ".gcov")
            if gcov_file.is_file():
                per_file[src] = _executed_lines_from_gcov(gcov_file)
            else:
                logger.warning("no se encontro %s", gcov_file)
        for leftover in root.glob("*.gcov"):
            leftover.unlink()
        coverage[test] = per_file
        logger.info("cobertura %-40s %s", test,
                    {k: len(v) for k, v in per_file.items()})
    return coverage


# ---------------------------------------------------------------------------
# Variante para proyectos CMake/Ninja (batch): los objetos viven en
# <build>/CMakeFiles/<target>.dir/... (anidados) y las fuentes pueden ser
# cabeceras (bibliotecas header-only), asi que se usa la salida JSON de gcov
# (`gcov --json-format --stdout <fichero.gcda>`), que informa de TODOS los
# ficheros (incluidas cabeceras) alcanzados por cada unidad de traduccion.
# ---------------------------------------------------------------------------

def _gcda_files(obj_dir: Path, sources: List[str]) -> List[str]:
    """Ficheros .gcda relevantes: los del propio fuente si es .c/.cpp; todos
    si alguna fuente es una cabecera (puede estar incluida desde cualquier TU)."""
    all_gcda = glob.glob(str(obj_dir / "**" / "*.gcda"), recursive=True)
    header_exts = (".h", ".hpp", ".hh", ".hxx", ".ipp", ".inl")
    if any(s.endswith(header_exts) for s in sources):
        return all_gcda
    names = {Path(s).name for s in sources}
    return [g for g in all_gcda
            if Path(g).name.rsplit(".gcda", 1)[0] in names]


def _relative_source(path_str: str, root: Path) -> str:
    p = Path(path_str)
    if not p.is_absolute():
        p = root / p
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return ""


def collect_coverage_json(root: Path,
                          sources: List[str],
                          object_dir: str,
                          run_cmd_for,
                          tests: List[str],
                          timeout_s: int,
                          deadline_s: int = 0,
                          shell: bool = True) -> Dict[str, Dict[str, Set[int]]]:
    """{test: {fuente_relativa: lineas_ejecutadas}} usando gcov JSON.

    `run_cmd_for(test)` devuelve el comando (shell) que ejecuta ese test contra
    el build instrumentado. Si se supera `deadline_s`, los tests restantes se
    omiten (quedan sin cobertura y, por tanto, fuera del dataset)."""
    import json
    import time

    coverage: Dict[str, Dict[str, Set[int]]] = {}
    obj_dir = root / object_dir
    wanted = set(sources)
    t0 = time.time()

    for test in tests:
        if deadline_s and time.time() - t0 > deadline_s:
            logger.warning("deadline de cobertura alcanzado: %d tests sin "
                           "cobertura", len(tests) - len(coverage))
            break
        for gcda in glob.glob(str(obj_dir / "**" / "*.gcda"), recursive=True):
            os.remove(gcda)
        cmd = run_cmd_for(test)
        try:
            subprocess.run(cmd if shell else cmd.split(), shell=shell,
                           cwd=root, timeout=timeout_s, capture_output=True)
        except subprocess.TimeoutExpired:
            logger.warning("timeout ejecutando %s durante la cobertura", test)

        per_file: Dict[str, Set[int]] = {s: set() for s in sources}
        for gcda in _gcda_files(obj_dir, sources):
            result = subprocess.run(
                ["gcov", "--json-format", "--stdout", gcda],
                cwd=obj_dir, capture_output=True, text=True,
                errors="replace")
            if result.returncode != 0 or not result.stdout.strip():
                continue
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                continue
            for entry in data.get("files", []):
                rel = _relative_source(entry.get("file", ""), root)
                if rel not in wanted:
                    continue
                for ln in entry.get("lines", []):
                    if ln.get("count", 0) > 0:
                        per_file[rel].add(int(ln["line_number"]))
        coverage[test] = per_file
        logger.info("cobertura %-40s %s", test[:40],
                    {k: len(v) for k, v in per_file.items()})
    return coverage
