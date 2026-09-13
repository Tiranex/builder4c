"""Runner de mutacion para proyectos C++: produce el "raw" estilo Major.

Flujo (equivalente al de Major sobre Java):
  1. build de linea base + descubrimiento de tests; todos deben pasar
  2. cobertura por test con gcov  ->  covMap.csv (test alcanza mutante)
  3. generacion de mutantes textuales (pmt.cpp_mutator)
  4. por cada mutante: aplicar, compilar (si no compila se descarta, como
     los mutantes no tipables de Major), ejecutar SOLO los tests que cubren
     su linea y clasificar el resultado:
         exit 0        -> pasa
         exit != 0     -> FAIL   (assert del test)
         senal (<0)    -> EXC    (excepcion no capturada, SIGFPE, SIGSEGV...)
         timeout       -> TIME
  5. escribir mutants.log, testMap.csv, covMap.csv, killMap.csv, kill.csv
     y summary.csv en el directorio de salida

Configuracion por proyecto (mutation.json junto al proyecto):
{
  "project": "Demo", "version": "1",
  "sources": ["src/calculator.cpp"],
  "test_sources": ["tests/test_calculator.cpp"],
  "build_cmd": "make -s all",
  "list_tests_cmd": "build/test_calculator --list",
  "run_test_cmd": "build/test_calculator {test}",
  "timeout_s": 5,
  "coverage": {
    "build_cmd": "make -s cov",
    "run_test_cmd": "build-cov/test_calculator {test}",
    "object_dir": "build-cov"
  }
}

Uso (desde builder4c/):
  python3 -m pmt.mutation_runner --config demo/demo___mathlib/mutation.json \
      --out demo/out/Demo_1/raw
"""

import argparse
import json
import logging
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .cpp_mutator import CppMutant, apply_mutant, generate_file_mutants
from .major_format import (
    MutantRecord,
    write_cov_map,
    write_kill_csv,
    write_kill_map,
    write_mutants_log,
    write_summary_csv,
    write_test_map,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BUILD_TIMEOUT_S = 300


def class_name_for(path: str) -> str:
    """'src/calculator.cpp' -> 'src.calculator' (analogo al paquete Java)."""
    p = Path(path)
    return ".".join(p.with_suffix("").parts)


def run_shell(cmd: str, cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, cwd=cwd, timeout=timeout,
                          capture_output=True, text=True)


def classify_test_run(cmd: str, cwd: Path, timeout: int,
                      shell: bool = False,
                      from_output: bool = False) -> Optional[str]:
    """None si el test pasa; FAIL/EXC/TIME si mata al mutante.

    Con `from_output` (modo ctest) el motivo se deduce de los marcadores del
    propio ctest (***Exception / ***Timeout), ya que ctest enmascara la senal
    del proceso del test con su propio codigo de salida."""
    try:
        result = subprocess.run(cmd if shell else cmd.split(), shell=shell,
                                cwd=cwd, timeout=timeout, capture_output=True)
    except subprocess.TimeoutExpired:
        return "TIME"
    if result.returncode == 0:
        return None
    if from_output:
        out = (result.stdout or b"") + (result.stderr or b"")
        if b"***Exception" in out:
            return "EXC"
        if b"***Timeout" in out:
            return "TIME"
        return "FAIL"
    if result.returncode < 0 or result.returncode >= 128:
        return "EXC"  # muerte por senal: abort/segfault/sigfpe...
    return "FAIL"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Mutacion estilo Major para C++")
    parser.add_argument("--config", required=True, help="ruta a mutation.json")
    parser.add_argument("--out", required=True, help="directorio de salida raw")
    parser.add_argument("--max-mutants", type=int, default=0,
                        help="limite de mutantes a procesar (0 = todos)")
    parser.add_argument("--deadline-s", type=int, default=0,
                        help="tiempo maximo de la fase de analisis (0 = sin limite)")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    root = config_path.parent
    with open(config_path, encoding="utf-8") as fh:
        config = json.load(fh)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    timeout_s = int(config.get("timeout_s", 10))
    build_timeout_s = int(config.get("build_timeout_s", BUILD_TIMEOUT_S))
    shell_tests = bool(config.get("shell_tests", False))
    from_output = bool(config.get("classify_from_output", False))
    anchor_regex = bool(config.get("anchor_regex_tests", False))
    mutant_budget_s = int(config.get("mutant_budget_s", 0))
    max_tests = int(config.get("max_tests", 0))
    t0 = time.time()

    def run_cmd_for(test_name: str) -> str:
        if anchor_regex:  # modo ctest: -R con el nombre anclado y escapado
            pattern = shlex.quote(f"^{re.escape(test_name)}$")
            return config["run_test_cmd"].format(test=pattern)
        return config["run_test_cmd"].format(test=test_name)

    # 1. linea base
    logger.info("build de linea base...")
    build = run_shell(config["build_cmd"], root, build_timeout_s)
    if build.returncode != 0:
        logger.error("fallo el build de linea base:\n%s", build.stderr[-2000:])
        return 1

    listing = run_shell(config["list_tests_cmd"], root, timeout_s * 20)
    test_ids = [l.strip() for l in listing.stdout.splitlines() if l.strip()]
    if not test_ids:
        logger.error("no se descubrieron tests")
        return 1

    # 1b. cobertura gcov (modo JSON, proyectos CMake): se calcula ANTES de
    # recortar a max_tests, para quedarnos con los tests que realmente
    # ejercitan los ficheros a mutar (si no, la mayoria de mutantes quedarian
    # sin cubrir y fuera del dataset).
    cov_config = config.get("coverage")
    coverage: Dict[str, Dict[str, Set[int]]] = {}
    if cov_config and cov_config.get("mode") == "gcov_json":
        logger.info("build instrumentado para cobertura...")
        build = run_shell(cov_config["build_cmd"], root, build_timeout_s)
        if build.returncode != 0:
            logger.error("fallo el build de cobertura:\n%s", build.stderr[-2000:])
            return 1
        from .coverage_gcov import collect_coverage_json

        def cov_cmd_for(test_name: str) -> str:
            if anchor_regex:
                pattern = shlex.quote(f"^{re.escape(test_name)}$")
                return cov_config["run_test_cmd"].format(test=pattern)
            return cov_config["run_test_cmd"].format(test=test_name)

        # prioridad: tests cuyo nombre menciona el fichero a mutar
        # (ring_buffer.c -> *ring_buffer*), luego los que comparten algun
        # token (ring, buffer), luego el resto en su orden original
        stems = [Path(s).stem.lower() for s in config["sources"]]
        tokens = {t for st in stems for t in re.split(r"[_\-.]", st)
                  if len(t) >= 4}

        def cov_priority(item):
            i, name = item
            low = name.lower()
            if any(st in low for st in stems):
                return (0, i)
            if any(t in low for t in tokens):
                return (1, i)
            return (2, i)

        cov_tests = [t for _, t in sorted(enumerate(test_ids),
                                          key=cov_priority)]
        cov_max = int(cov_config.get("max_tests", 0))
        if cov_max:
            cov_tests = cov_tests[:cov_max]
        coverage = collect_coverage_json(
            root, config["sources"], cov_config["object_dir"], cov_cmd_for,
            cov_tests, timeout_s, int(cov_config.get("deadline_s", 0)),
            shell=shell_tests)
        # tests que tocan alguna fuente, los que mas lineas cubren primero
        scored = [(sum(len(v) for v in coverage[t].values()), i, t)
                  for i, t in enumerate(test_ids) if t in coverage]
        scored = [x for x in scored if x[0] > 0]
        scored.sort(key=lambda x: (-x[0], x[1]))
        test_ids = [t for _, _, t in scored]
        logger.info("%d tests cubren alguna de las fuentes a mutar",
                    len(test_ids))
        if not test_ids:
            logger.error("ningun test cubre las fuentes a mutar")
            return 1
    if max_tests:
        test_ids = test_ids[:max_tests]

    if config.get("test_class"):
        test_class = config["test_class"]
    else:
        test_class = class_name_for(config["test_sources"][0])
    logger.info("descubiertos %d tests", len(test_ids))

    passing_tests = []
    for name in test_ids:
        verdict = classify_test_run(run_cmd_for(name), root, timeout_s,
                                    shell=shell_tests, from_output=from_output)
        if verdict is None:
            passing_tests.append(name)
        elif config.get("drop_failing_tests"):
            logger.warning("descartado test rojo en linea base: %s (%s)",
                           name, verdict)
        else:
            logger.error("el test %s no pasa en la linea base (%s)", name, verdict)
            return 1
    test_ids = passing_tests
    if not test_ids:
        logger.error("ningun test pasa en la linea base")
        return 1

    test_map: Dict[int, str] = {
        i + 1: f"{test_class}[{name}]" for i, name in enumerate(test_ids)
    }
    no_by_test = {name: i + 1 for i, name in enumerate(test_ids)}
    logger.info("linea base verde con %d tests", len(test_ids))

    # 2. cobertura por test (modo clasico: binario propio + gcov de texto)
    if cov_config and cov_config.get("mode") == "gcov_json":
        pass  # ya calculada en 1b
    elif cov_config:
        logger.info("build instrumentado para cobertura...")
        build = run_shell(cov_config["build_cmd"], root, BUILD_TIMEOUT_S)
        if build.returncode != 0:
            logger.error("fallo el build de cobertura:\n%s", build.stderr[-2000:])
            return 1
        from .coverage_gcov import collect_coverage
        coverage = collect_coverage(root, config["sources"],
                                    cov_config["object_dir"],
                                    cov_config["run_test_cmd"],
                                    test_ids, timeout_s)
    else:
        logger.warning("sin configuracion de cobertura: se asume que todos "
                       "los tests cubren todos los mutantes")

    def covering_tests(src: str, line: int) -> List[str]:
        if not cov_config:
            return list(test_ids)
        return [t for t in test_ids if line in coverage.get(t, {}).get(src, set())]

    # 3. candidatos
    candidates: List[Tuple[str, CppMutant]] = []
    original: Dict[str, str] = {}
    for src in config["sources"]:
        text = (root / src).read_text(encoding="utf-8")
        original[src] = text
        for mut in generate_file_mutants(text, class_name_for(src)):
            candidates.append((src, mut))
    candidates.sort(key=lambda sm: (sm[0], sm[1].line, sm[1].start_off))
    logger.info("generados %d mutantes candidatos", len(candidates))
    if cov_config:
        # un mutante no cubierto no genera fila en el dataset: no merece
        # compilarse ni ejecutarse
        covered_lines = {src: set() for src in config["sources"]}
        for per_file in coverage.values():
            for src, lines in per_file.items():
                covered_lines.setdefault(src, set()).update(lines)
        candidates = [(s_, m) for s_, m in candidates
                      if m.line in covered_lines.get(s_, set())]
        logger.info("%d candidatos en lineas cubiertas", len(candidates))
    if args.max_mutants and len(candidates) > args.max_mutants:
        # muestreo uniforme (determinista) a lo largo de ficheros y lineas,
        # en vez de quedarnos con los primeros N del primer fichero
        n = len(candidates)
        idx = sorted({(i * n) // args.max_mutants
                      for i in range(args.max_mutants)})
        candidates = [candidates[i] for i in idx]
        logger.info("muestreados %d candidatos", len(candidates))

    preproc_s = time.time() - t0
    t1 = time.time()

    # 4. ejecucion mutante a mutante
    records: List[MutantRecord] = []
    cov_pairs: List[Tuple[int, int]] = []
    kill_triples: List[Tuple[int, int, str]] = []
    status_by_mutant: Dict[int, str] = {}
    discarded = 0
    next_no = 1

    deadline = (t1 + args.deadline_s) if args.deadline_s else None

    for src, mut in candidates:
        if deadline and time.time() > deadline:
            logger.warning("deadline de analisis alcanzado: se detiene el "
                           "procesado de mutantes (parcial)")
            break
        mutated = apply_mutant(original[src], mut)
        src_path = root / src
        try:
            src_path.write_text(mutated, encoding="utf-8")
            build = run_shell(config["build_cmd"], root, build_timeout_s)
            if build.returncode != 0:
                discarded += 1
                continue  # mutante no compilable: descartado (no valido)
            mutant_no = next_no
            mutant_t0 = time.time()
            over_budget = False
            new_cov: List[Tuple[int, int]] = []
            new_kills: List[Tuple[int, int, str]] = []
            killed_reason = None
            for test in covering_tests(src, mut.line):
                if mutant_budget_s and time.time() - mutant_t0 > mutant_budget_s:
                    over_budget = True
                    break
                test_no = no_by_test[test]
                new_cov.append((test_no, mutant_no))
                verdict = classify_test_run(run_cmd_for(test), root, timeout_s,
                                            shell=shell_tests,
                                            from_output=from_output)
                if verdict is not None:
                    new_kills.append((test_no, mutant_no, verdict))
                    if killed_reason is None:
                        killed_reason = verdict
            if over_budget:
                # matriz incompleta: el mutante se descarta entero para no
                # contaminar covMap/killMap con datos parciales
                discarded += 1
                logger.warning("mutante %s:%d fuera de presupuesto: descartado",
                               src, mut.line)
                continue
            next_no += 1
            records.append(MutantRecord(
                mutant_no=mutant_no, operator=mut.operator,
                from_desc=mut.from_desc, to_desc=mut.to_desc,
                class_name=mut.class_name, method=mut.method_sig,
                line=mut.line, before=mut.before, after=mut.after))
            cov_pairs.extend(new_cov)
            kill_triples.extend(new_kills)
            status_by_mutant[mutant_no] = killed_reason or "LIVE"
            logger.info("mutante %-4d %s:%d %-4s %-30s -> %s",
                        mutant_no, src, mut.line, mut.operator,
                        (mut.before + " => " + mut.after)[:30],
                        status_by_mutant[mutant_no])
        finally:
            src_path.write_text(original[src], encoding="utf-8")

    # restaurar build limpio
    run_shell(config["build_cmd"], root, build_timeout_s)
    analysis_s = time.time() - t1

    # 5. salida raw estilo Major
    write_mutants_log(out_dir / "mutants.log", records)
    write_test_map(out_dir / "testMap.csv", test_map)
    cov_pairs.sort()
    kill_triples.sort()
    write_cov_map(out_dir / "covMap.csv", cov_pairs)
    write_kill_map(out_dir / "killMap.csv", kill_triples)
    write_kill_csv(out_dir / "kill.csv", status_by_mutant)

    covered = {m for _, m in cov_pairs}
    killed = {m for _, m, _ in kill_triples}
    live = covered - killed
    write_summary_csv(out_dir / "summary.csv", len(records), len(covered),
                      len(killed), len(live), preproc_s, analysis_s)

    logger.info("hecho: %d mutantes validos (%d descartados por no compilar), "
                "%d cubiertos, %d muertos, %d vivos",
                len(records), discarded, len(covered), len(killed), len(live))
    logger.info("raw escrito en %s", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
