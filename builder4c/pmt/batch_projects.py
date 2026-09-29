"""Orquestador batch: dataset PMT para todos los proyectos de projects_v1.

Por cada proyecto con project.json:
  1. clone superficial (al commit fijado si lo hay)
  2. configure con CMake + Ninja (flags del project.json, saneadas)
  3. build con presupuesto de tiempo
  4. descubrimiento de tests via ctest; los tests rojos en linea base se
     descartan (drop_failing_tests)
  5. seleccion heuristica de ficheros fuente a mutar (verificados contra
     compile_commands.json para asegurar que forman parte del build)
  6. pmt.mutation_runner con presupuestos (max mutantes, deadline global,
     presupuesto por mutante) -> raw estilo Major
  7. pmt.build_dataset -> [Proyecto]_1_results.csv + _test_map.csv

El estado va quedando en <out>/progress.log (una linea por evento) y el
resultado final en <out>/report.json y <out>/report.md.

Uso (desde builder4c/, dentro de WSL/Linux):
  python3 -m pmt.batch_projects --projects projects_v1 \
      --work ~/pmt_batch --out out_batch
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# presupuestos (segundos) por fase
CLONE_TIMEOUT = 600
CONFIGURE_TIMEOUT = 600
BUILD_TIMEOUT = 1500
LIST_TIMEOUT = 120
BASELINE_TEST_TIMEOUT = 30
MUTATION_DEADLINE = 1500
MUTANT_BUDGET = 240
MAX_TESTS = 20
MAX_MUTANTS = 100
CTEST_TIMEOUT = 25
# cobertura real con gcov (build paralelo instrumentado con --coverage)
COVERAGE_BUILD_TIMEOUT = 1500
COVERAGE_MAX_TESTS = 150       # tests sobre los que se mide cobertura
COVERAGE_DEADLINE = 900        # tope de la fase de cobertura

# orden: primero los previsiblemente pequenos para acumular exitos pronto
PROJECT_ORDER = [
    "awslabs___aws-c-common",
    "CLIUtils___CLI11",
    "emil-e___rapidcheck",
    "morwenn___cpp-sort",
    "nanomsg___nng",
    "libevent___libevent",
    "zeromq___libzmq",
    "CESNET___libyang",
    "skypjack___entt",
    "uncrustify___uncrustify",
    "KhronosGroup___SPIRV-Tools",
    "SOCI___soci",
    "danmar___cppcheck",
    "facebook___rocksdb",
    "DynamoRIO___dynamorio",
    "apache___arrow",
    "llvm___llvm-project",
]

EXCLUDE_SRC_RE = re.compile(
    r"(^|/)(tests?|testing|unittests?|examples?|bench|benchmarks?|"
    r"third[_-]?party|3rdparty|external|extern|tools|fuzz(ing)?|docs?|"
    r"samples?|perf|contrib|deps|vendor|cmake|scripts?)(/|$)", re.I)

# ficheros de test que viven junto a las fuentes (nng: bus_test.c, leveldb:
# env_posix_test.cc, c_test.c...): mutarlos no tiene sentido para el dataset
EXCLUDE_TEST_FILE_RE = re.compile(
    r"(^|/)(test_[^/]*|[^/]*[_\-.]tests?|[^/]*Tests?|[^/]*_utest)\.(c|cc|cpp|cxx)$")

SRC_EXTS = (".cpp", ".cc", ".cxx", ".c")
HDR_EXTS = (".hpp", ".hh", ".h", ".ipp")


@dataclass
class ProjectResult:
    project: str
    stage: str = "inicio"       # ultima fase alcanzada
    ok: bool = False
    detail: str = ""
    commit: str = ""            # HEAD real del checkout mutado
    tests: int = 0
    sources: List[str] = field(default_factory=list)
    mutants: int = 0
    covered: int = 0
    killed: int = 0
    live: int = 0
    rows: int = 0
    seconds: float = 0.0


class Progress:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())


def run(cmd, cwd: Path, timeout: int, shell: bool = False):
    return subprocess.run(cmd, shell=shell, cwd=cwd, timeout=timeout,
                          capture_output=True, text=True)


def clone_project(repo_url: str, commit: str, repo_dir: Path,
                  progress: Progress, timeout: int) -> Tuple[bool, str]:
    if (repo_dir / ".git").exists():
        head = run(["git", "rev-parse", "--verify", "HEAD"], repo_dir, 60)
        if head.returncode == 0:
            # una tanda interrumpida (docker stop) puede dejar un fuente
            # mutado: se restauran los ficheros versionados antes de seguir
            restored = run(["git", "status", "--short", "--untracked-files=no"],
                           repo_dir, 60).stdout.strip()
            if restored:
                run(["git", "checkout", "--", "."], repo_dir, 120)
                progress.log("  clone: restaurados ficheros modificados: "
                             + " ".join(l.split()[-1] for l in restored.splitlines())[:200])
            progress.log(f"  clone: reutilizando checkout existente")
            return True, "reutilizado"
        # clone anterior a medias (fetch fallido): se rehace desde cero
        progress.log("  clone: checkout incompleto, se rehace")
        shutil.rmtree(repo_dir, ignore_errors=True)
    repo_dir.mkdir(parents=True, exist_ok=True)
    try:
        if commit:
            for cmd in (["git", "init", "-q"],
                        ["git", "remote", "add", "origin", repo_url],
                        ["git", "fetch", "-q", "--depth", "1", "origin", commit],
                        ["git", "checkout", "-q", "FETCH_HEAD"]):
                r = run(cmd, repo_dir, timeout)
                if r.returncode != 0:
                    return False, f"{' '.join(cmd[:3])}: {r.stderr.strip()[:200]}"
        else:
            r = run(["git", "clone", "-q", "--depth", "1", repo_url,
                     str(repo_dir)], repo_dir.parent, timeout)
            if r.returncode != 0:
                return False, f"clone: {r.stderr.strip()[:200]}"
        run(["git", "submodule", "update", "--init", "--recursive",
             "--depth", "1"], repo_dir, timeout)
        return True, "ok"
    except subprocess.TimeoutExpired:
        return False, "timeout en clone"


def find_cmake_source(repo_dir: Path, build_flags: List[str]) -> Optional[Path]:
    for flag in build_flags:
        if flag.startswith("-S=") or flag.startswith("-S "):
            return repo_dir / flag[3:].strip()
    if (repo_dir / "CMakeLists.txt").is_file():
        return repo_dir
    for sub in ("llvm", "cpp", "src"):
        if (repo_dir / sub / "CMakeLists.txt").is_file():
            return repo_dir / sub
    return None


def sanitize_flags(build_flags: List[str]) -> List[str]:
    out = []
    for flag in build_flags:
        if flag.startswith("-S=") or "COMPILER_LAUNCHER" in flag:
            continue
        out.append(flag)
    return out


def compiled_files(build_dir: Path, repo_dir: Path) -> Optional[set]:
    cc = build_dir / "compile_commands.json"
    if not cc.is_file():
        return None
    try:
        entries = json.loads(cc.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    files = set()
    for e in entries:
        f = e.get("file", "")
        try:
            files.add(str(Path(f).resolve().relative_to(repo_dir.resolve())))
        except ValueError:
            continue
    return files


def select_sources(repo_dir: Path, build_dir: Path, cmake_src: Path,
                   max_files: int = 3,
                   explicit: Optional[List[str]] = None) -> List[str]:
    """Ficheros a mutar. Con `explicit` (override `sources`) se respetan
    los que existen en el repo, avisando de los que no forman parte del
    build segun compile_commands.json; si no, seleccion heuristica."""
    compiled = compiled_files(build_dir, repo_dir)

    if explicit:
        chosen = []
        for rel in explicit:
            if not (repo_dir / rel).is_file():
                print(f"  aviso: fuente explicita inexistente, se omite: {rel}",
                      flush=True)
                continue
            if compiled is not None and Path(rel).suffix in SRC_EXTS \
                    and rel not in compiled:
                print(f"  aviso: {rel} no aparece en compile_commands.json",
                      flush=True)
            chosen.append(rel)
        return chosen

    def candidates(exts) -> List[Tuple[int, str]]:
        found = []
        for path in repo_dir.rglob("*"):
            if path.suffix not in exts or not path.is_file():
                continue
            rel = path.relative_to(repo_dir).as_posix()
            if EXCLUDE_SRC_RE.search(rel) or EXCLUDE_TEST_FILE_RE.search(rel):
                continue
            size = path.stat().st_size
            if not (1500 <= size <= 60000):
                continue
            if compiled is not None and path.suffix in SRC_EXTS \
                    and rel not in compiled:
                continue  # no forma parte del build: mutarlo seria inutil
            found.append((size, rel))
        return found

    cands = candidates(SRC_EXTS)
    if not cands:
        # bibliotecas header-only: mutar cabeceras bajo include/ (o, si no
        # hay, bajo src/ o cualquier otra cabecera no excluida: entt, json...)
        headers = candidates(HDR_EXTS)
        cands = [(s, r) for s, r in headers
                 if r.startswith("include/") or "/include/" in r]
        if not cands:
            cands = [(s, r) for s, r in headers if r.startswith("src/")]
        if not cands:
            cands = headers
    # preferencia por tamano medio (mas funciones utiles, builds razonables)
    cands.sort(key=lambda sr: abs(sr[0] - 12000))
    return [rel for _, rel in cands[:max_files]]


def process_project(name: str, projects_dir: Path, work_root: Path,
                    out_root: Path, pmt_cwd: Path, progress: Progress,
                    budget_s: int = 0,
                    max_mutants: int = MAX_MUTANTS,
                    smoke: bool = False) -> ProjectResult:
    t0 = time.time()
    result = ProjectResult(project=name)
    short = name.split("___")[-1]

    def left() -> float:
        """Segundos restantes del presupuesto total del proyecto."""
        if not budget_s:
            return 10 ** 9
        return budget_s - (time.time() - t0)

    config_file = projects_dir / name / "project.json"
    if not config_file.is_file():
        result.stage, result.detail = "config", "sin project.json"
        return result
    project = json.loads(config_file.read_text(encoding="utf-8"))
    repo_url = project.get("main_repo", "")
    commit = project.get("commit_hash", "") or ""
    build_flags = sanitize_flags(
        project.get("c_compile", {}).get("build_flags", []))
    # ajustes propios por proyecto (no vienen de project.json):
    #   {"cmake_flags": ["-DX=Y"], "c_flags": "...", "cxx_flags": "..."}
    overrides_file = projects_dir / name / "pmt_overrides.json"
    overrides = {}
    if overrides_file.is_file():
        overrides = json.loads(overrides_file.read_text(encoding="utf-8"))
        progress.log(f"  overrides: {overrides}")
    build_flags += [f for f in overrides.get("cmake_flags", [])
                    if f.startswith("-D")]
    extra_c = overrides.get("c_flags", "")
    extra_cxx = overrides.get("cxx_flags", "")
    ctest_timeout = int(overrides.get("ctest_timeout", CTEST_TIMEOUT))
    # limites ampliables por proyecto (ver GUIA §10 y HANDOFF_ampliacion):
    #   sources (lista explicita), max_sources, max_tests (0 = todos los que
    #   cubren), max_mutants, mutant_budget_s, mutation_deadline_s,
    #   coverage_max_tests, coverage_deadline_s, test_expansion
    #   ("gtest"|"cmocka"|"catch2"), expand_tests (regex: que tests ctest se
    #   expanden), exclude_tests (regex sobre nombres ctest)
    max_tests = int(overrides.get("max_tests", MAX_TESTS))
    max_mutants = int(overrides.get("max_mutants", max_mutants))
    mutant_budget = int(overrides.get("mutant_budget_s", MUTANT_BUDGET))
    mutation_deadline = int(overrides.get("mutation_deadline_s", MUTATION_DEADLINE))
    cov_max_tests = int(overrides.get("coverage_max_tests", COVERAGE_MAX_TESTS))
    cov_deadline_cap = int(overrides.get("coverage_deadline_s", COVERAGE_DEADLINE))
    test_expansion = str(overrides.get("test_expansion", "") or "")
    exclude_tests = str(overrides.get("exclude_tests", "") or "")
    if smoke:
        # prueba de humo: mismo camino completo, limites minimos
        max_tests = min(max_tests or 10, 10)
        max_mutants = min(max_mutants, 5)
        cov_max_tests = min(cov_max_tests, 40)
        mutation_deadline = min(mutation_deadline, 900)
        progress.log("  modo --smoke: 10 tests, 5 mutantes, 40 tests de cobertura")

    def flag_args(c_base: str = "", cxx_base: str = "") -> List[str]:
        out = []
        c = " ".join(x for x in (c_base, extra_c) if x)
        cxx = " ".join(x for x in (cxx_base, extra_cxx) if x)
        if c:
            out.append(f"-DCMAKE_C_FLAGS={c}")
        if cxx:
            out.append(f"-DCMAKE_CXX_FLAGS={cxx}")
        return out

    work = work_root / name
    repo_dir = work / "repo"
    build_dir = work / "build"
    out_dir = out_root / f"{short}_1"
    raw_dir = out_dir / "raw"

    # 1. clone
    result.stage = "clone"
    ok, detail = clone_project(repo_url, commit, repo_dir, progress,
                               int(min(CLONE_TIMEOUT, max(30, left()))))
    if not ok:
        result.detail = detail
        return result
    progress.log(f"  clone ok ({detail})")
    head = run(["git", "rev-parse", "HEAD"], repo_dir, 60)
    result.commit = head.stdout.strip() if head.returncode == 0 else ""
    progress.log(f"  commit: {result.commit or '?'}"
                 + ("" if commit else "  (HEAD sin fijar en project.json)"))
    for pre_cmd in overrides.get("pre_configure", []):
        # p. ej. SPIRV-Tools: "python3 utils/git-sync-deps" (deps en external/)
        try:
            r = run(pre_cmd, repo_dir, CLONE_TIMEOUT, shell=True)
        except subprocess.TimeoutExpired:
            result.stage, result.detail = "pre_configure", f"timeout: {pre_cmd}"
            return result
        if r.returncode != 0:
            result.stage = "pre_configure"
            result.detail = f"{pre_cmd}: {(r.stderr or r.stdout).strip()[-250:]}"
            return result
        progress.log(f"  pre_configure ok: {pre_cmd}")

    # 2. configure
    result.stage = "configure"
    cmake_src = find_cmake_source(repo_dir, project.get("c_compile", {})
                                  .get("build_flags", []))
    if cmake_src is None:
        result.detail = "no se encontro CMakeLists.txt"
        return result
    if left() < 30:
        result.detail = "presupuesto de proyecto agotado tras el clone"
        return result
    cmd = (["cmake", "-S", str(cmake_src), "-B", str(build_dir), "-G", "Ninja",
            "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
            "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"]
           + flag_args()
           + [f for f in build_flags if f.startswith("-D")])
    try:
        r = run(cmd, work, int(min(CONFIGURE_TIMEOUT, max(30, left()))))
    except subprocess.TimeoutExpired:
        result.detail = "timeout en configure"
        return result
    if r.returncode != 0:
        result.detail = (r.stderr.strip() or r.stdout.strip())[-300:]
        return result
    progress.log("  configure ok")

    # 3. build
    result.stage = "build"
    build_budget = int(min(BUILD_TIMEOUT, max(30, left())))
    try:
        r = run(["ninja", "-C", str(build_dir)], work, build_budget)
    except subprocess.TimeoutExpired:
        result.detail = f"timeout en build ({build_budget}s)"
        return result
    if r.returncode != 0:
        result.detail = (r.stdout + r.stderr)[-300:]
        return result
    progress.log("  build ok")

    # 4. tests disponibles
    result.stage = "tests"
    try:
        r = run(["ctest", "--test-dir", str(build_dir), "-N"], work, LIST_TIMEOUT)
    except subprocess.TimeoutExpired:
        result.detail = "timeout listando tests"
        return result
    test_names = re.findall(r"^\s*Test\s+#\d+:\s+(.+?)\s*$", r.stdout, re.M)
    if not test_names:
        result.detail = "ctest no lista ningun test"
        return result
    progress.log(f"  {len(test_names)} tests descubiertos")

    # 5. seleccion de fuentes
    result.stage = "fuentes"
    sources = select_sources(repo_dir, build_dir, cmake_src,
                             max_files=int(overrides.get("max_sources", 3)),
                             explicit=overrides.get("sources"))
    if not sources:
        result.detail = "sin ficheros fuente candidatos"
        return result
    result.sources = sources
    progress.log(f"  fuentes a mutar: {sources}")

    # 5b. build instrumentado para cobertura (gcov). Si falla, se sigue sin
    # cobertura (covMap asumira que todos los tests cubren todo).
    result.stage = "cobertura"
    cov_build_dir = work / "build-cov"
    coverage_cfg = None
    cov_flags = "--coverage -O0"
    cmd_cov = (["cmake", "-S", str(cmake_src), "-B", str(cov_build_dir),
                "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Debug",
                "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"]
               + flag_args(cov_flags, cov_flags) +
               ["-DCMAKE_EXE_LINKER_FLAGS=--coverage",
                "-DCMAKE_SHARED_LINKER_FLAGS=--coverage"]
               + [f for f in build_flags if f.startswith("-D")])
    # con presupuesto por proyecto, la cobertura no puede comerse mas de la
    # mitad de lo que queda (el resto se reserva para la mutacion)
    cov_budget = int(min(COVERAGE_BUILD_TIMEOUT, max(60, left() / 2)))
    cov_deadline = int(min(cov_deadline_cap, max(60, left() / 3)))
    try:
        r = run(cmd_cov, work, int(min(CONFIGURE_TIMEOUT, max(30, left()))))
        if r.returncode == 0:
            r = run(["ninja", "-C", str(cov_build_dir)], work, cov_budget)
        if r.returncode == 0:
            coverage_cfg = {
                "mode": "gcov_json",
                "build_cmd": f"ninja -C {cov_build_dir}",
                "run_test_cmd":
                    f"ctest --test-dir {cov_build_dir} --timeout {ctest_timeout} "
                    "-R {test}",
                "object_dir": str(cov_build_dir),
                "max_tests": cov_max_tests,
                "deadline_s": cov_deadline,
            }
            progress.log("  build de cobertura ok")
        else:
            progress.log("  build de cobertura FALLO; se sigue sin cobertura: "
                         + (r.stdout + r.stderr)[-200:].replace("\n", " "))
    except subprocess.TimeoutExpired:
        progress.log("  build de cobertura: timeout; se sigue sin cobertura")

    # 6. mutacion
    result.stage = "mutacion"
    mutation_config = {
        "project": short,
        "version": "1",
        "sources": sources,
        "build_cmd": f"ninja -C {build_dir}",
        "list_tests_cmd":
            f"ctest --test-dir {build_dir} -N | "
            "sed -n 's/^ *Test *#[0-9]*: //p'",
        "run_test_cmd":
            f"ctest --test-dir {build_dir} --timeout {ctest_timeout} -R {{test}}",
        "timeout_s": ctest_timeout + 10,
        "build_timeout_s": BUILD_TIMEOUT,
        "shell_tests": True,
        "anchor_regex_tests": True,
        "classify_from_output": True,
        "drop_failing_tests": True,
        "max_tests": max_tests,
        "mutant_budget_s": mutant_budget,
        "test_class": f"ctest.{short}",
        # enlace test -> codigo fuente (test_sources.csv) y sub-tests
        "ctest_build_dir": str(build_dir),
        "repo_dir": str(repo_dir),
        "test_expansion": test_expansion,
        "expand_tests": str(overrides.get("expand_tests", "") or ""),
        "test_workers": int(overrides.get("test_workers", 1)),
        "exclude_tests": exclude_tests,
        "meta": {
            "project": short,
            "project_dir": name,
            "repo_url": repo_url,
            "commit": result.commit,
            "commit_pinned": bool(commit),
        },
    }
    if coverage_cfg:
        mutation_config["coverage"] = coverage_cfg
        mutation_config["build_timeout_s"] = max(BUILD_TIMEOUT,
                                                 COVERAGE_BUILD_TIMEOUT)
    (repo_dir / "mutation.json").write_text(
        json.dumps(mutation_config, indent=2), encoding="utf-8")
    raw_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "mutation.log"
    # el deadline de la mutacion descuenta la fase de cobertura (que corre
    # dentro de mutation_runner) del presupuesto restante del proyecto
    mut_deadline = int(min(mutation_deadline,
                           max(120, left() - (cov_deadline if coverage_cfg
                                              else 0))))
    progress.log(f"  mutacion: hasta {max_mutants} mutantes, "
                 f"deadline {mut_deadline}s")
    try:
        with open(log_path, "w", encoding="utf-8") as log_fh:
            proc = subprocess.run(
                [sys.executable, "-m", "pmt.mutation_runner",
                 "--config", str(repo_dir / "mutation.json"),
                 "--out", str(raw_dir),
                 "--max-mutants", str(max_mutants),
                 "--deadline-s", str(mut_deadline)],
                cwd=pmt_cwd, stdout=log_fh, stderr=subprocess.STDOUT,
                timeout=(mut_deadline + mutation_config["build_timeout_s"] * 2
                         + (cov_deadline if coverage_cfg else 0) + 600))
    except subprocess.TimeoutExpired:
        result.detail = "mutation_runner: timeout duro (presupuesto agotado)"
        return result
    if proc.returncode != 0:
        tail = "".join(open(log_path, encoding="utf-8",
                            errors="replace").readlines()[-4:])
        result.detail = f"mutation_runner fallo: {tail[-300:]}"
        return result

    summary = (raw_dir / "summary.csv").read_text(encoding="utf-8").splitlines()
    gen, cov, kil, liv = summary[1].split(",")[:4]
    result.mutants, result.covered = int(gen), int(cov)
    result.killed, result.live = int(kil), int(liv)
    test_map_lines = (raw_dir / "testMap.csv").read_text(
        encoding="utf-8").count("\n") - 1
    result.tests = max(test_map_lines, 0)
    progress.log(f"  mutacion: {gen} mutantes, {cov} cubiertos, "
                 f"{kil} muertos, {liv} vivos")

    # 7. dataset final
    result.stage = "dataset"
    proc = subprocess.run(
        [sys.executable, "-m", "pmt.build_dataset",
         "--input", str(raw_dir), "--project", short, "--version", "1",
         "--language", "cpp", "--src-root", str(repo_dir),
         "--keep-missing-source", "--out", str(out_dir)],
        cwd=pmt_cwd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        result.detail = f"build_dataset fallo: {proc.stderr[-300:]}"
        return result
    results_csv = out_dir / f"{short}_1_results.csv"
    result.rows = results_csv.read_text(
        encoding="utf-8", errors="replace").count("\n") - 1
    if result.mutants == 0 or result.rows <= 0:
        result.detail = "sin filas en el dataset"
        return result

    result.stage, result.ok = "completo", True
    result.detail = "ok"
    result.seconds = time.time() - t0
    return result


def write_report(results: List[ProjectResult], out_root: Path) -> None:
    # fusion con el reporte previo (relanzar con --only no borra el resto)
    previous = {}
    report_json = out_root / "report.json"
    if report_json.is_file():
        try:
            for entry in json.loads(report_json.read_text(encoding="utf-8")):
                previous[entry["project"]] = ProjectResult(**entry)
        except (json.JSONDecodeError, TypeError):
            previous = {}
    merged = list(previous.values())
    for r in results:
        if r.project in previous:
            merged[merged.index(previous[r.project])] = r
        else:
            merged.append(r)
    results = merged
    data = [vars(r) for r in results]
    (out_root / "report.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Reporte batch PMT — projects_v1", "",
             "| Proyecto | Estado | Fase | Commit | Tests | Mutantes | Cubiertos | "
             "Muertos | Vivos | Filas | Detalle |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        estado = "OK" if r.ok else "FALLO"
        lines.append(
            f"| {r.project} | {estado} | {r.stage} | {(r.commit or '')[:10]} "
            f"| {r.tests} | {r.mutants} "
            f"| {r.covered} | {r.killed} | {r.live} | {r.rows} "
            f"| {' '.join(r.detail.split())[:120].replace('|', '/')} |")
    (out_root / "report.md").write_text("\n".join(lines) + "\n",
                                        encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", action="append", default=[],
                        help="procesar solo estos proyectos")
    parser.add_argument("--project-budget", type=int, default=0,
                        help="segundos maximos por proyecto (clone+build+"
                             "cobertura+mutacion); 0 = sin limite")
    parser.add_argument("--max-mutants", type=int, default=MAX_MUTANTS,
                        help=f"mutantes por proyecto (defecto {MAX_MUTANTS}; "
                             "los pmt_overrides.json tienen prioridad)")
    parser.add_argument("--smoke", action="store_true",
                        help="prueba de humo: recorre todo el pipeline con "
                             "10 tests, 5 mutantes y 40 tests de cobertura")
    args = parser.parse_args(argv)

    projects_dir = Path(args.projects).resolve()
    work_root = Path(os.path.expanduser(args.work)).resolve()
    out_root = Path(args.out).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    pmt_cwd = Path(__file__).resolve().parent.parent  # builder4c/

    progress = Progress(out_root / "progress.log")

    names = [n for n in PROJECT_ORDER if (projects_dir / n).is_dir()]
    for extra in sorted(p.name for p in projects_dir.iterdir() if p.is_dir()):
        if extra not in names:
            names.append(extra)
    if args.only:
        names = [n for n in names if n in args.only]

    results: List[ProjectResult] = []
    for i, name in enumerate(names, 1):
        progress.log(f"=== [{i}/{len(names)}] {name} ===")
        try:
            result = process_project(name, projects_dir, work_root, out_root,
                                     pmt_cwd, progress,
                                     budget_s=args.project_budget,
                                     max_mutants=args.max_mutants,
                                     smoke=args.smoke)
        except Exception as exc:  # noqa: BLE001 - el batch debe continuar
            result = ProjectResult(project=name, stage="excepcion",
                                   detail=repr(exc)[:300])
        result.seconds = result.seconds or 0.0
        results.append(result)
        estado = "OK" if result.ok else f"FALLO en {result.stage}"
        progress.log(f"=== {name}: {estado} ({result.detail[:150]}) ===")
        write_report(results, out_root)  # reporte incremental

    progress.log("BATCH TERMINADO: " +
                 f"{sum(1 for r in results if r.ok)}/{len(results)} proyectos OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
