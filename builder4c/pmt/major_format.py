"""Lectura y escritura de los ficheros en bruto con el formato de Major.

Formatos (identicos a los que produce Major para Java):

mutants.log   una linea por mutante generado:
              <id>:<OP>:<from>:<to>:<Clase>[@<metodo(firma)>]:<linea>:<antes> |==> <despues>
testMap.csv   TestNo,TestName          (TestName con formato Clase[metodo])
covMap.csv    TestNo,MutantNo          (el test cubre/alcanza al mutante)
killMap.csv   TestNo,MutantNo,[FAIL | TIME | EXC]
kill.csv      MutantNo,[FAIL | TIME | EXC | LIVE]  (estado final por mutante)
summary.csv   totales de la ejecucion

Regla clave: si un par <TestNo,MutantNo> esta en covMap pero no en killMap,
el mutante sobrevivio a ese test.
"""

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

MUTATION_SEP = " |==> "


@dataclass
class MutantRecord:
    mutant_no: int
    operator: str            # ROR, AOR, COR, LVR, STD, ...
    from_desc: str           # p.ej. ==(int,int) o <CALL>
    to_desc: str             # p.ej. <=(int,int) o <NO-OP>
    class_name: str          # p.ej. org.apache.commons.csv.CSVFormat / src.calculator
    method: Optional[str]    # p.ej. isLineBreak(char); None si no hay metodo
    line: int                # linea (1-based) en el fichero fuente
    before: str              # codigo antes de la mutacion
    after: str               # codigo despues de la mutacion

    @property
    def qualified_location(self) -> str:
        return f"{self.class_name}@{self.method}" if self.method else self.class_name

    def to_log_line(self) -> str:
        # el formato separa campos por ':' -> las firmas C++ no pueden llevar
        # '::' (std::string -> std.string, como un paquete Java)
        location = self.qualified_location.replace("::", ".")
        return (
            f"{self.mutant_no}:{self.operator}:{self.from_desc}:{self.to_desc}:"
            f"{location}:{self.line}:{self.before}{MUTATION_SEP}{self.after}"
        )


_MUTANT_LINE_RE = re.compile(
    r"^(\d+):([^:]+):([^:]*):([^:]*):(.+?):(\d+):(.*)$", re.S)


def parse_mutant_line(line: str) -> MutantRecord:
    line = line.rstrip("\n")
    parts = line.split(":", 6)
    if len(parts) != 7 or not parts[5].isdigit():
        # firmas C++ con '::' (raws antiguos): localizacion no-greedy hasta
        # el primer ':<linea>:'
        m = _MUTANT_LINE_RE.match(line)
        if not m:
            raise ValueError(f"linea de mutants.log no reconocida: {line!r}")
        parts = list(m.groups())
    location = parts[4]
    if "@" in location:
        class_name, method = location.split("@", 1)
    else:
        class_name, method = location, None
    code = parts[6]
    before, sep, after = code.partition(MUTATION_SEP)
    if not sep:
        before, after = code, ""
    return MutantRecord(
        mutant_no=int(parts[0]),
        operator=parts[1],
        from_desc=parts[2],
        to_desc=parts[3],
        class_name=class_name,
        method=method,
        line=int(parts[5]),
        before=before,
        after=after,
    )


def read_mutants_log(path: Path) -> Dict[int, MutantRecord]:
    mutants: Dict[int, MutantRecord] = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            if not raw.strip():
                continue
            rec = parse_mutant_line(raw)
            mutants[rec.mutant_no] = rec
    return mutants


def write_mutants_log(path: Path, mutants: List[MutantRecord]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for rec in mutants:
            fh.write(rec.to_log_line() + "\n")


def read_test_map(path: Path) -> Dict[int, str]:
    tests: Dict[int, str] = {}
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)  # cabecera TestNo,TestName
        for row in reader:
            if len(row) >= 2:
                tests[int(row[0])] = row[1]
    return tests


def write_test_map(path: Path, tests: Dict[int, str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["TestNo", "TestName"])
        for test_no in sorted(tests):
            writer.writerow([test_no, tests[test_no]])


def read_cov_map(path: Path) -> List[Tuple[int, int]]:
    pairs: List[Tuple[int, int]] = []
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)  # cabecera TestNo,MutantNo
        for row in reader:
            if len(row) >= 2:
                pairs.append((int(row[0]), int(row[1])))
    return pairs


def write_cov_map(path: Path, pairs: List[Tuple[int, int]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["TestNo", "MutantNo"])
        for test_no, mutant_no in pairs:
            writer.writerow([test_no, mutant_no])


def read_kill_map(path: Path) -> List[Tuple[int, int, str]]:
    triples: List[Tuple[int, int, str]] = []
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)  # cabecera TestNo,MutantNo,[FAIL | TIME | EXC]
        for row in reader:
            if len(row) >= 3:
                triples.append((int(row[0]), int(row[1]), row[2].strip()))
    return triples


def write_kill_map(path: Path, triples: List[Tuple[int, int, str]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["TestNo", "MutantNo", "[FAIL | TIME | EXC]"])
        for test_no, mutant_no, reason in triples:
            writer.writerow([test_no, mutant_no, reason])


def write_kill_csv(path: Path, status_by_mutant: Dict[int, str]) -> None:
    """kill.csv: estado final por mutante (FAIL/TIME/EXC del primer kill, o LIVE)."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["MutantNo", "[FAIL | TIME | EXC | LIVE]"])
        for mutant_no in sorted(status_by_mutant):
            writer.writerow([mutant_no, status_by_mutant[mutant_no]])


def write_summary_csv(path: Path, generated: int, covered: int, killed: int,
                      live: int, preproc_s: float, analysis_s: float) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["MutantsGenerated", "MutantsCovered", "MutantsKilled",
                         "MutantsLive", "RuntimePreprocSeconds", "RuntimeAnalysisSeconds"])
        writer.writerow([generated, covered, killed, live,
                         round(preproc_s, 2), round(analysis_s, 2)])


def dotted_test_name(raw_name: str) -> str:
    """Convierte 'paquete.Clase[metodo]' (testMap) a 'paquete.Clase.metodo' (dataset)."""
    if raw_name.endswith("]") and "[" in raw_name:
        cls, _, method = raw_name.rstrip("]").partition("[")
        return f"{cls}.{method}"
    return raw_name


def split_test_name(raw_name: str) -> Tuple[str, str]:
    """Divide 'paquete.Clase[metodo]' en ('paquete.Clase', 'metodo')."""
    if raw_name.endswith("]") and "[" in raw_name:
        cls, _, method = raw_name.rstrip("]").partition("[")
        return cls, method
    return raw_name, ""
