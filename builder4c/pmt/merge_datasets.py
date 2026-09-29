"""Concatena los datasets por proyecto del batch en un unico dataset PMT.

Recorre <out>/<Proyecto>_1/<Proyecto>_1_results.csv y _test_map.csv y escribe
<out>/pmt_dataset_results.csv y <out>/pmt_dataset_test_map.csv con el mismo
esquema (19 columnas / 2 columnas). Los `TestMethod` ya van prefijados por
proyecto (`ctest.<proyecto>.<test>`), asi que no hay colisiones.

Uso (desde builder4c/):
  python3 -m pmt.merge_datasets --out out_batch_docker
"""

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path


def merge(out_root: Path) -> int:
    results_files = sorted(out_root.glob("*_1/*_1_results.csv"))
    if not results_files:
        print("no hay *_1_results.csv en", out_root, file=sys.stderr)
        return 1

    header = None
    rows = []
    per_project = Counter()
    status = Counter()
    for path in results_files:
        with open(path, encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            head = next(reader)
            if header is None:
                header = head
            elif head != header:
                print(f"cabecera distinta en {path}", file=sys.stderr)
                return 1
            for row in reader:
                rows.append(row)
                per_project[path.parent.name] += 1
                status[row[header.index("Status")]] += 1

    with open(out_root / "pmt_dataset_results.csv", "w", encoding="utf-8",
              newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)

    tm_rows = []
    for path in sorted(out_root.glob("*_1/*_1_test_map.csv")):
        with open(path, encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            next(reader)
            tm_rows.extend(reader)
    with open(out_root / "pmt_dataset_test_map.csv", "w", encoding="utf-8",
              newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["TestMethod", "TestMethodCode"])
        w.writerows(tm_rows)

    # enlace test -> codigo fuente (fichero, linea, URL al commit), si existe
    ts_header = None
    ts_rows = []
    for path in sorted(out_root.glob("*_1/*_1_test_sources.csv")):
        with open(path, encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            head = next(reader, None)
            if head is None:
                continue
            ts_header = ts_header or head
            ts_rows.extend(reader)
    if ts_header:
        with open(out_root / "pmt_dataset_test_sources.csv", "w",
                  encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(ts_header)
            w.writerows(ts_rows)

    # commit y fuentes mutadas de cada proyecto (meta.json por proyecto)
    metas = {}
    for path in sorted(out_root.glob("*_1/*_1_meta.json")):
        try:
            metas[path.parent.name] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
    if metas:
        (out_root / "pmt_dataset_meta.json").write_text(
            json.dumps(metas, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"{len(rows)} filas de mutantes de {len(per_project)} proyectos "
          f"-> {out_root / 'pmt_dataset_results.csv'}")
    for proj, n in per_project.most_common():
        commit = (metas.get(proj) or {}).get("commit", "")
        print(f"  {proj:40s} {n:5d}  {commit[:12]}")
    print("Status:", dict(status))
    print(f"{len(tm_rows)} tests -> {out_root / 'pmt_dataset_test_map.csv'}")
    if ts_header:
        linked = sum(1 for r in ts_rows if len(r) > 1 and r[1])
        print(f"{linked}/{len(ts_rows)} tests con enlace a su codigo -> "
              f"{out_root / 'pmt_dataset_test_sources.csv'}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True,
                        help="directorio de salida del batch")
    args = parser.parse_args(argv)
    return merge(Path(args.out))


if __name__ == "__main__":
    sys.exit(main())
