"""Verificacion del dataset PMT de un proyecto (salida de batch_projects).

Dos niveles:

  estatico  (siempre)  recalcula, con codigo independiente de pmt.dataset,
            todo lo derivable del raw y del codigo fuente en el commit:
            - esquema de 19 columnas y 2 columnas del test_map
            - Status/Label/KillingTests/PassingTests/Tests contra killMap y
              covMap; Tests = Killing + Passing; sin solapes
            - todo test citado existe en el test_map; nombres unicos
            - SrcMethodKey / MutSrcLineNo / Body / BeforePMT / AfterPMT
            - la linea mutada del fichero (en el commit) contiene `Before`
            - SrcLines[MutSrcLineNo] == Body
            - TestMethodCode: lista de str valida; % de tests con codigo
            - test_sources: fichero existe, linea en rango, el nombre del
              caso aparece en su linea (o el bloque extraido lo contiene),
              SourceUrl apunta al commit; meta.json con el commit de
              project.json
  dinamico  (--replay N, dentro del contenedor, con el repo y el build del
            batch): reaplica N mutantes KILLED y N SURVIVED, recompila y
            reejecuta sus tests: los KILLED deben fallar con al menos un test
            de KillingTests y los SURVIVED deben pasar todos sus tests. Antes
            comprueba que el build original pasa esos mismos tests.

Uso (desde builder4c/):
  python3 -m pmt.verify_dataset --project-dir out_run3_aws/aws-c-common_1 \
      --repo /pmt_work/awslabs___aws-c-common/repo \
      --projects projects_v1 [--replay 10]

Codigo de salida: 0 si no hay errores (los avisos no cuentan).
"""

import argparse
import ast
import csv
import json
import random
import re
import shlex
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

csv.field_size_limit(1 << 30)

RESULT_COLUMNS = [
    "Class", "Count", "Method", "Line", "SrcMethodKey", "Status", "Label",
    "Tests", "KillingTests", "PassingTests", "Operator", "SrcLines",
    "MutSrcLineNo", "Before", "After", "BeforePMT", "AfterPMT", "Body",
    "MatchingIdx",
]
OPERATORS = {"ROR", "AOR", "COR", "LVR", "STD"}
CPP_EXTS = [".cpp", ".cc", ".cxx", ".c", ".hpp", ".hh", ".h"]


class Report:
    def __init__(self, name: str):
        self.name = name
        self.errors: Counter = Counter()
        self.warnings: Counter = Counter()
        self.examples: Dict[str, List[str]] = defaultdict(list)
        self.stats: Dict[str, object] = {}

    def error(self, kind: str, example: str = "") -> None:
        self.errors[kind] += 1
        if example and len(self.examples[kind]) < 3:
            self.examples[kind].append(example)

    def warn(self, kind: str, example: str = "") -> None:
        self.warnings[kind] += 1
        if example and len(self.examples[kind]) < 3:
            self.examples[kind].append(example)

    def to_dict(self) -> dict:
        return {"project": self.name, "errors": dict(self.errors),
                "warnings": dict(self.warnings), "examples": dict(self.examples),
                "stats": self.stats}

    def print(self) -> None:
        print(f"== {self.name}")
        for k, v in self.stats.items():
            print(f"   {k}: {v}")
        for kind, n in sorted(self.errors.items()):
            print(f"   ERROR {kind}: {n}")
            for ex in self.examples.get(kind, []):
                print(f"         p.ej. {ex[:220]}")
        for kind, n in sorted(self.warnings.items()):
            print(f"   aviso {kind}: {n}")
            for ex in self.examples.get(kind, []):
                print(f"         p.ej. {ex[:220]}")
        print("   RESULTADO:", "OK" if not self.errors else "ERRORES")


# ─────────────────────────── lectura independiente ───────────────────────────

def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def raw_test_map(raw: Path) -> Dict[int, str]:
    out = {}
    with open(raw / "testMap.csv", encoding="utf-8", newline="") as fh:
        r = csv.reader(fh)
        next(r, None)
        for row in r:
            out[int(row[0])] = row[1]
    return out


def dotted(raw_name: str) -> str:
    if raw_name.endswith("]") and "[" in raw_name:
        cls, _, m = raw_name[:-1].partition("[")
        return f"{cls}.{m}"
    return raw_name


def raw_id(raw_name: str) -> str:
    if raw_name.endswith("]") and "[" in raw_name:
        return raw_name[:-1].partition("[")[2]
    return raw_name


def pairs(path: Path, n: int) -> List[Tuple]:
    out = []
    with open(path, encoding="utf-8", newline="") as fh:
        r = csv.reader(fh)
        next(r, None)
        for row in r:
            if len(row) >= n:
                out.append(tuple(int(x) if i < 2 else x for i, x in enumerate(row[:n])))
    return out


def mutant_lines(raw: Path) -> Dict[int, Dict[str, object]]:
    """mutants.log: no:OP:from:to:clase[@metodo]:linea:before |==> after."""
    out = {}
    for line in (raw / "mutants.log").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        head, _, after = line.partition(" |==> ")
        parts = head.split(":", 6)
        if len(parts) < 7:
            continue
        no, op, _frm, _to, cls_meth, ln, before = parts
        cls = cls_meth.split("@", 1)[0]
        out[int(no)] = {"op": op, "class": cls, "line": int(ln),
                        "before": before, "after": after}
    return out


def literal_list(text: str) -> Optional[List[str]]:
    try:
        v = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return v
    return None


def pmt_tokens(expr: str) -> str:
    from .source_extract import pmt_tokens as tok
    return tok(expr)


def class_file(repo: Path, cls: str) -> Optional[Path]:
    base = cls.replace(".", "/")
    for ext in CPP_EXTS:
        p = repo / (base + ext)
        if p.is_file():
            return p
    return None


# ─────────────────────────────── estatico ────────────────────────────────

def verify_static(pdir: Path, repo: Optional[Path], projects: Optional[Path],
                  rep: Report) -> Dict:
    raw = pdir / "raw"
    prefix = pdir.name
    res_path = pdir / f"{prefix}_results.csv"
    tm_path = pdir / f"{prefix}_test_map.csv"
    header, rows = read_csv(res_path)
    if header != RESULT_COLUMNS:
        rep.error("cabecera_results", str(header))
    tm_header, tm_rows = read_csv(tm_path)
    if tm_header != ["TestMethod", "TestMethodCode"]:
        rep.error("cabecera_test_map", str(tm_header))

    tmap = raw_test_map(raw)
    name_by_no = {no: dotted(n) for no, n in tmap.items()}
    cov = pairs(raw / "covMap.csv", 2)
    kill = pairs(raw / "killMap.csv", 3)
    covered_by: Dict[int, Set[int]] = defaultdict(set)
    for t, m in cov:
        covered_by[m].add(t)
    killed_by: Dict[int, Set[int]] = defaultdict(set)
    for t, m, _ in kill:
        killed_by[m].add(t)
        if t not in covered_by[m]:
            rep.error("kill_sin_cobertura", f"test {t} mutante {m}")
    muts = mutant_lines(raw)

    # --- test_map
    names = [r["TestMethod"] for r in tm_rows]
    dup = [n for n, c in Counter(names).items() if c > 1]
    for n in dup:
        rep.error("test_duplicado", n)
    tm_names = set(names)
    if set(name_by_no.values()) != tm_names:
        rep.error("test_map_distinto_de_testMap",
                  f"{len(tm_names ^ set(name_by_no.values()))} diferencias")
    with_code = 0
    for r in tm_rows:
        code = literal_list(r["TestMethodCode"])
        if code is None:
            rep.error("TestMethodCode_invalido", r["TestMethod"])
        elif code:
            with_code += 1
        else:
            rep.warn("test_sin_codigo", r["TestMethod"])

    # --- filas de mutantes
    status_c: Counter = Counter()
    ops: Counter = Counter()
    file_cache: Dict[str, Optional[List[str]]] = {}
    seen_mutants = set()
    for r in rows:
        mid = f"{r['Class']}#{r['Count']}"
        try:
            no = int(r["Count"])
            line = int(r["Line"])
            rel = int(r["MutSrcLineNo"])
        except ValueError:
            rep.error("campo_no_numerico", mid)
            continue
        if no in seen_mutants:
            rep.error("mutante_duplicado", mid)
        seen_mutants.add(no)
        m = muts.get(no)
        if m is None:
            rep.error("mutante_no_en_mutants_log", mid)
            continue
        if (m["line"], m["op"], m["class"]) != (line, r["Operator"], r["Class"]):
            rep.error("fila_distinta_de_mutants_log", mid)
        status_c[r["Status"]] += 1
        ops[r["Operator"]] += 1
        if r["Operator"] not in OPERATORS:
            rep.error("operador_desconocido", mid)
        exp_kill = {name_by_no[t] for t in killed_by.get(no, set())}
        exp_pass = {name_by_no[t] for t in covered_by.get(no, set())} - exp_kill
        if not covered_by.get(no):
            rep.error("fila_de_mutante_no_cubierto", mid)
        k = literal_list(r["KillingTests"])
        p = literal_list(r["PassingTests"])
        t = literal_list(r["Tests"])
        if k is None or p is None or t is None:
            rep.error("lista_de_tests_invalida", mid)
            continue
        if set(k) != exp_kill:
            rep.error("KillingTests_distinto_de_killMap", mid)
        if set(p) != exp_pass:
            rep.error("PassingTests_distinto_de_covMap", mid)
        if t != k + p:
            rep.error("Tests_no_es_Killing_mas_Passing", mid)
        if set(k) & set(p):
            rep.error("test_que_mata_y_pasa", mid)
        for name in t:
            if name not in tm_names:
                rep.error("test_no_en_test_map", f"{mid}: {name}")
        exp_status = "KILLED" if exp_kill else "SURVIVED"
        if r["Status"] != exp_status:
            rep.error("Status_incorrecto", mid)
        if r["Label"] != ("1" if exp_status == "KILLED" else "0"):
            rep.error("Label_incorrecto", mid)
        # clave de metodo y posicion relativa
        km = re.fullmatch(re.escape(r["Class"]) + r"_(\d+)_(\d+)", r["SrcMethodKey"])
        if not km:
            rep.error("SrcMethodKey_formato", mid)
        else:
            start, end = int(km.group(1)), int(km.group(2))
            if rel != line - start or not (start <= line <= end):
                rep.error("MutSrcLineNo_incorrecto", mid)
        src_lines = literal_list(r["SrcLines"])
        if src_lines is None:
            rep.error("SrcLines_invalido", mid)
        elif not (0 <= rel < len(src_lines)) or src_lines[rel] != r["Body"]:
            rep.error("SrcLines_no_contiene_Body", mid)
        if r["Before"] != m["before"] or (r["After"] != m["after"] and
                                           not (r["Operator"] == "STD" and r["After"] == "NOOP")):
            rep.error("Before_After_distinto_de_mutants_log", mid)
        if r["BeforePMT"] != pmt_tokens(r["Before"]):
            rep.error("BeforePMT_incorrecto", mid)
        exp_after = "<noop>," if r["Operator"] == "STD" else pmt_tokens(r["After"])
        if r["AfterPMT"] != exp_after:
            rep.error("AfterPMT_incorrecto", mid)
        if r["MatchingIdx"] != "":
            rep.error("MatchingIdx_no_vacio", mid)
        if repo is not None:
            key = r["Class"]
            if key not in file_cache:
                f = class_file(repo, key)
                file_cache[key] = (f.read_text(encoding="utf-8", errors="replace")
                                   .splitlines() if f else None)
            lines = file_cache[key]
            if lines is None:
                rep.error("fichero_fuente_no_encontrado", key)
            elif not (1 <= line <= len(lines)):
                rep.error("linea_fuera_de_fichero", mid)
            else:
                src = lines[line - 1]
                if lines[line - 1].strip() != r["Body"]:
                    rep.error("Body_distinto_del_fuente", mid)
                if " ".join(r["Before"].split()) not in " ".join(src.split()) and \
                        r["Operator"] != "STD":
                    rep.warn("Before_no_literal_en_linea", mid)

    killed_rows = status_c.get("KILLED", 0)
    rep.stats.update({
        "filas": len(rows),
        "KILLED/SURVIVED": f"{killed_rows}/{status_c.get('SURVIVED', 0)}",
        "operadores": dict(ops),
        "tests_test_map": len(tm_rows),
        "tests_con_codigo": f"{with_code}/{len(tm_rows)}",
        "mutantes_mutants_log": len(muts),
    })

    # --- test_sources + meta
    ts_path = pdir / f"{prefix}_test_sources.csv"
    meta_path = pdir / f"{prefix}_meta.json"
    meta = {}
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        rep.stats["commit"] = meta.get("commit", "")
        if projects is not None and meta.get("project_dir"):
            pj = projects / meta["project_dir"] / "project.json"
            if pj.is_file():
                pinned = json.loads(pj.read_text(encoding="utf-8")).get("commit_hash", "")
                if pinned and pinned != meta.get("commit"):
                    rep.error("commit_distinto_del_fijado",
                              f"{meta.get('commit')} != {pinned}")
                if not pinned:
                    rep.warn("commit_sin_fijar_en_project_json", meta["project_dir"])
    else:
        rep.error("sin_meta_json", str(meta_path))
    if not ts_path.is_file():
        rep.error("sin_test_sources", str(ts_path))
        return {"tmap": tmap, "muts": muts, "rows": rows,
                "killed_by": killed_by, "covered_by": covered_by}
    _, ts_rows = read_csv(ts_path)
    ts_names = {r["TestMethod"] for r in ts_rows}
    for n in tm_names - ts_names:
        rep.error("test_sin_fila_en_test_sources", n)
    gran = Counter(r["Granularity"] for r in ts_rows)
    strat = Counter(r["Strategy"] for r in ts_rows)
    rep.stats["granularidad"] = dict(gran)
    rep.stats["estrategias"] = dict(strat)
    commit = meta.get("commit", "")
    for r in ts_rows:
        name = r["TestMethod"]
        if r["Strategy"] == "none":
            rep.warn("test_sin_enlace", name)
            continue
        if commit and commit not in r["SourceUrl"]:
            rep.error("SourceUrl_sin_commit", name)
        if repo is None:
            continue
        f = repo / r["TestFile"]
        if not f.is_file():
            rep.error("TestFile_inexistente", f"{name}: {r['TestFile']}")
            continue
        flines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        ln = int(r["TestLine"] or 0)
        if not (1 <= ln <= len(flines)):
            rep.error("TestLine_fuera_de_rango", name)
            continue
        fn = r["TestFunction"]
        if r["Granularity"] == "ctest" and "add_test" not in flines[ln - 1]:
            rep.error("definicion_ctest_sin_add_test", f"{name} @ {r['TestFile']}:{ln}")
            continue
        if r["Granularity"] == "macro":
            continue
        if fn.startswith("Scenario: "):
            fn = fn[len("Scenario: "):]      # SCENARIO(...) antepone el prefijo
        if r["Granularity"] in ("function", "section") and fn:
            window = "\n".join(flines[max(0, ln - 2): ln + 2])
            needle = fn.split(".")[-1] if r["Strategy"] == "gtest_filter" else fn
            if needle not in window and needle.replace('"', '\\"') not in window:
                # TEMPLATE_TEST_CASE y nombres generados: el nombre listado
                # lleva sufijos (" - int") que no estan en el fuente
                base = needle.split(" - ")[0]
                if base not in window:
                    rep.warn("nombre_del_caso_no_en_su_linea", f"{name} @ {r['TestFile']}:{ln}")
    return {"tmap": tmap, "muts": muts, "rows": rows,
            "killed_by": killed_by, "covered_by": covered_by}


# ─────────────────────────────── dinamico ────────────────────────────────

def load_mutation_config(repo: Path) -> dict:
    cfg = repo / "mutation.json"
    return json.loads(cfg.read_text(encoding="utf-8"))


def build_command(cfg: dict, ctest_tests: Dict[str, List[str]], ctest_wd: Dict[str, str],
                  test_id: str, expanded: bool, limit_s: int = 0) -> str:
    """Comando equivalente al del runner (reimplementado para verificar),
    con el mismo limite de tiempo por test que uso la mutacion."""
    mode = (cfg.get("test_expansion") or "").lower()
    base, sub = (test_id.split("/", 1) if expanded else (test_id, ""))
    if sub and mode == "catch2":
        exe = ctest_tests[base][0]
        wd = ctest_wd.get(base) or str(Path(exe).parent)
        esc = sub.replace("\\", "\\\\").replace('"', '\\"').replace(",", "\\,")
        limit = limit_s or max(1, int(cfg.get("timeout_s", 35)) - 2)
        return (f"cd {shlex.quote(wd)} && exec timeout -k 5 {limit} "
                f"{shlex.quote(exe)} {shlex.quote(chr(34) + esc + chr(34))}")
    target = shlex.quote(f"^{re.escape(base)}$")
    env = ""
    if sub and mode == "gtest":
        env = f"GTEST_FILTER={shlex.quote(sub)} "
    elif sub and mode == "cmocka":
        env = ("LD_PRELOAD=/usr/local/lib/libcmocka_filter.so "
               f"CMOCKA_TEST_FILTER={shlex.quote(sub)} ")
    cmd = cfg["run_test_cmd"].format(test=target)
    if limit_s:
        cmd = re.sub(r"--timeout\s+\d+", f"--timeout {limit_s}", cmd)
    return env + cmd


def run_test(cmd: str, cwd: Path, timeout: int) -> str:
    """PASS / FAIL / EXC / TIME (misma semantica que el runner)."""
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, timeout=timeout, capture_output=True)
    except subprocess.TimeoutExpired:
        return "TIME"
    if r.returncode == 0:
        return "PASS"
    out = (r.stdout or b"") + (r.stderr or b"")
    if b"***Exception" in out:
        return "EXC"
    if b"***Timeout" in out or r.returncode == 124:
        return "TIME"
    if r.returncode >= 128:
        return "EXC"
    return "FAIL"


def verify_replay(pdir: Path, repo: Path, data: Dict, n: int, seed: int,
                  rep: Report) -> None:
    from .cpp_mutator import apply_mutant, generate_file_mutants
    cfg = load_mutation_config(repo)
    raw = pdir / "raw"
    ctest_json = json.loads((raw / "ctest_tests.json").read_text(encoding="utf-8")) \
        if (raw / "ctest_tests.json").is_file() else {"tests": []}
    ctest_cmd = {t["name"]: t.get("command") or [] for t in ctest_json.get("tests", [])}
    ctest_wd = {}
    for t in ctest_json.get("tests", []):
        for prop in t.get("properties", []) or []:
            if prop.get("name") == "WORKING_DIRECTORY":
                ctest_wd[t["name"]] = prop.get("value", "")
    tmap = data["tmap"]
    ids = {no: raw_id(name) for no, name in tmap.items()}
    locations: Dict[int, Tuple[int, int]] = {}
    if (raw / "mutant_locations.csv").is_file():
        with open(raw / "mutant_locations.csv", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                locations[int(row["MutantNo"])] = (int(row["StartOff"]), int(row["EndOff"]))

    def is_expanded(tid: str) -> bool:
        return "/" in tid and tid not in ctest_cmd and tid.split("/", 1)[0] in ctest_cmd

    limits: Dict[int, int] = {}
    if (raw / "test_timeouts.csv").is_file():
        with open(raw / "test_timeouts.csv", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                limits[int(row["TestNo"])] = int(row["LimitSeconds"])

    def cmd_for(no: int) -> str:
        tid = ids[no]
        return build_command(cfg, ctest_cmd, ctest_wd, tid, is_expanded(tid),
                             limits.get(no, 0))

    timeout = int(cfg.get("timeout_s", 35))
    build_cmd = cfg["build_cmd"]
    rows = data["rows"]
    rng = random.Random(seed)
    killed = [r for r in rows if r["Status"] == "KILLED"]
    survived = [r for r in rows if r["Status"] == "SURVIVED"]
    sample = rng.sample(killed, min(n, len(killed))) + rng.sample(survived, min(n, len(survived)))
    # indice de sources -> fichero relativo
    src_by_class = {}
    for src in cfg["sources"]:
        src_by_class[".".join(Path(src).with_suffix("").parts)] = src

    print(f"   replay: build de linea base...", flush=True)
    b = subprocess.run(build_cmd, shell=True, cwd=repo, capture_output=True, text=True,
                       timeout=int(cfg.get("build_timeout_s", 1500)))
    if b.returncode != 0:
        rep.error("replay_build_base_fallo", b.stderr[-300:])
        return
    replay_ok = Counter()
    t0 = time.time()
    for r in sample:
        no = int(r["Count"])
        m = data["muts"][no]
        src = src_by_class.get(r["Class"])
        if not src:
            rep.error("replay_fuente_desconocida", r["Class"])
            continue
        path = repo / src
        original = path.read_text(encoding="utf-8")
        cands = [c for c in generate_file_mutants(original, r["Class"])
                 if c.line == m["line"] and c.operator == m["op"]
                 and c.before == m["before"] and c.after == m["after"]]
        loc = locations.get(no)
        if loc is not None:
            exact = [c for c in cands if (c.start_off, c.end_off) == loc]
            if not exact:
                rep.error("replay_offsets_no_casan", f"#{no} {src}:{m['line']}")
                continue
            cands = exact
        if not cands:
            rep.error("replay_mutante_no_regenerable", f"#{no} {src}:{m['line']}")
            continue
        if len(cands) > 1:
            rep.warn("replay_mutante_ambiguo", f"#{no} {src}:{m['line']} ({len(cands)})")
        killers = sorted(data["killed_by"].get(no, set()))
        covering = sorted(data["covered_by"].get(no, set()))
        tests = killers[:2] if killers else covering
        # base: esos tests pasan sin mutar
        base_bad = [t for t in tests if run_test(cmd_for(t), repo, timeout + 30) != "PASS"]
        if base_bad:
            rep.warn("replay_test_rojo_sin_mutar", f"#{no}: {ids[base_bad[0]]}")
            continue
        try:
            path.write_text(apply_mutant(original, cands[0]), encoding="utf-8")
            b = subprocess.run(build_cmd, shell=True, cwd=repo, capture_output=True,
                               text=True, timeout=int(cfg.get("build_timeout_s", 1500)))
            if b.returncode != 0:
                rep.error("replay_mutante_no_compila", f"#{no} {src}:{m['line']}")
                continue
            verdicts = {t: run_test(cmd_for(t), repo, timeout + 30) for t in tests}
        finally:
            path.write_text(original, encoding="utf-8")
            # sin recompilar, la linea base del siguiente mutante se mediria
            # contra el binario de este
            subprocess.run(build_cmd, shell=True, cwd=repo, capture_output=True,
                           timeout=int(cfg.get("build_timeout_s", 1500)))
        if r["Status"] == "KILLED":
            if all(v == "PASS" for v in verdicts.values()):
                rep.error("replay_KILLED_no_reproducido",
                          f"#{no} {src}:{m['line']} {m['before']} -> {m['after']}")
            else:
                replay_ok["KILLED"] += 1
        else:
            bad = [ids[t] for t, v in verdicts.items() if v != "PASS"]
            if bad:
                rep.error("replay_SURVIVED_falla_un_test",
                          f"#{no} {src}:{m['line']} {bad[0]} ({verdicts})")
            else:
                replay_ok["SURVIVED"] += 1
        print(f"   replay #{no} {r['Status']}: {dict(verdicts) if len(verdicts) < 4 else len(verdicts)}"
              f" ({time.time() - t0:.0f}s)", flush=True)
    subprocess.run(build_cmd, shell=True, cwd=repo, capture_output=True, timeout=1500)
    rep.stats["replay"] = (f"KILLED {replay_ok['KILLED']}/{min(n, len(killed))}, "
                           f"SURVIVED {replay_ok['SURVIVED']}/{min(n, len(survived))}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verificacion del dataset PMT de un proyecto")
    ap.add_argument("--project-dir", required=True, action="append",
                    help="directorio <P>_1 (repetible)")
    ap.add_argument("--repo", action="append", default=[],
                    help="repo del proyecto en el commit (mismo orden que --project-dir)")
    ap.add_argument("--projects", help="projects_v1 (para comprobar el commit fijado)")
    ap.add_argument("--replay", type=int, default=0,
                    help="reejecutar N mutantes KILLED y N SURVIVED (contenedor)")
    ap.add_argument("--seed", type=int, default=20260925)
    ap.add_argument("--json-out", help="guardar el informe en JSON")
    args = ap.parse_args(argv)

    reports = []
    for i, pd in enumerate(args.project_dir):
        pdir = Path(pd)
        repo = Path(args.repo[i]) if i < len(args.repo) else None
        rep = Report(pdir.name)
        data = verify_static(pdir, repo, Path(args.projects) if args.projects else None, rep)
        if args.replay and repo is not None:
            verify_replay(pdir, repo, data, args.replay, args.seed, rep)
        rep.print()
        reports.append(rep)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps([r.to_dict() for r in reports], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    return 0 if all(not r.errors for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
