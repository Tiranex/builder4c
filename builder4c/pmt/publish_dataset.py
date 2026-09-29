"""Monta el dataset publicable a partir de las salidas del batch.

  python3 -m pmt.publish_dataset --dest ../dataset \
      --run out_run3_aws --run out_run3_ly --run out_run3_catch \
      --work /pmt_work [--legacy-name legacy_2026-09-09]

Hace:
  1. si `dest` ya tiene contenido, lo mueve a `dest/<legacy-name>/` (no borra)
  2. copia cada `<run>/<P>_1/` a `dest/projects/<P>_1/`
  3. `dest/projects/<P>_1/source_snapshot/`: copia, desde el checkout del
     batch (`<work>/<proyecto>/repo`, que esta en el commit fijado), los
     ficheros fuente mutados y los ficheros de test enlazados en
     `<P>_1_test_sources.csv`, con su ruta relativa al repo
  4. concatena los CSV (pmt.merge_datasets) en `dest/`
  5. une los report.json de cada run en `dest/report.json` / `report.md`

Se ejecuta dentro del contenedor (necesita `<work>`); sin `--work` omite el
paso 3.
"""

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

csv.field_size_limit(1 << 30)


def snapshot(pdir: Path, repo: Path) -> int:
    prefix = pdir.name
    meta = json.loads((pdir / f"{prefix}_meta.json").read_text(encoding="utf-8"))
    files = set(meta.get("sources", []))
    ts = pdir / f"{prefix}_test_sources.csv"
    if ts.is_file():
        with open(ts, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("TestFile"):
                    files.add(row["TestFile"])
    out = pdir / "source_snapshot"
    n = 0
    for rel in sorted(files):
        src = repo / rel
        if not src.is_file() or Path(rel).is_absolute() or ".." in Path(rel).parts:
            continue
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    (out / "COMMIT").write_text(
        f"{meta.get('repo_url', '')} @ {meta.get('commit', '')}\n", encoding="utf-8")
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True)
    ap.add_argument("--run", action="append", required=True)
    ap.add_argument("--work", default="")
    ap.add_argument("--legacy-name", default="legacy")
    args = ap.parse_args(argv)

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    legacy = dest / args.legacy_name
    existing = [p for p in dest.iterdir() if not p.name.startswith("legacy")]
    if existing and not legacy.exists():
        # primera publicacion sobre un dataset anterior: se aparta entero
        legacy.mkdir()
        for p in existing:
            shutil.move(str(p), str(legacy / p.name))
        print(f"contenido anterior movido a {legacy}")
    elif existing:
        # republicacion: el legacy ya existe y no se toca; los ficheros
        # generados se sustituyen y el resto (README, verificacion) se conserva
        for name in ("projects", "pmt_dataset_results.csv", "pmt_dataset_test_map.csv",
                     "pmt_dataset_test_sources.csv", "pmt_dataset_meta.json",
                     "report.json", "report.md"):
            target = dest / name
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
    projects = dest / "projects"
    projects.mkdir(exist_ok=True)

    reports = []
    for run in args.run:
        run = Path(run)
        for pdir in sorted(run.glob("*_1")):
            if not (pdir / f"{pdir.name}_results.csv").is_file():
                continue
            target = projects / pdir.name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(pdir, target,
                            ignore=shutil.ignore_patterns("mutation.log"))
            shutil.copy2(pdir / "mutation.log", target / "raw" / "mutation.log")
            if args.work:
                meta = json.loads((target / f"{pdir.name}_meta.json").read_text(encoding="utf-8"))
                repo = Path(args.work) / meta.get("project_dir", "") / "repo"
                if repo.is_dir():
                    print(f"{pdir.name}: {snapshot(target, repo)} ficheros en source_snapshot/")
        rj = run / "report.json"
        if rj.is_file():
            reports.extend(json.loads(rj.read_text(encoding="utf-8")))

    from .merge_datasets import merge
    rc = merge(projects)
    for name in ("pmt_dataset_results.csv", "pmt_dataset_test_map.csv",
                 "pmt_dataset_test_sources.csv", "pmt_dataset_meta.json"):
        if (projects / name).is_file():
            shutil.move(str(projects / name), str(dest / name))

    (dest / "report.json").write_text(json.dumps(reports, indent=2, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
    lines = ["# Reporte batch PMT — tanda 3", "",
             "| Proyecto | Estado | Commit | Tests | Mutantes | Cubiertos | Muertos | Vivos | Filas | Horas |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in reports:
        lines.append(f"| {r['project']} | {'OK' if r.get('ok') else 'FALLO'} | "
                     f"{(r.get('commit') or '')[:10]} | {r['tests']} | {r['mutants']} | "
                     f"{r['covered']} | {r['killed']} | {r['live']} | {r['rows']} | "
                     f"{r.get('seconds', 0) / 3600:.1f} |")
    (dest / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
