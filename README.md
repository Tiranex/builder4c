# Dataset PMT para C++ (Predictive Mutation Testing)

Este repositorio contiene:

- **`dataset/`** (generado localmente, excluido de git) — el dataset final de
  mutantes de proyectos C++ reales, con
  exactamente el mismo esquema (19 columnas) que los datasets PMT generados en
  Java con la herramienta [Major](https://mutation-testing.org/), más su
  `test_map` y un reporte por proyecto. Ver [`dataset/README.md`](dataset/README.md).
- **`builder4c/pmt/`** — el pipeline que lo genera (Python 3, solo librería
  estándar): un "Major para C++" que genera mutantes (ROR, AOR, COR, LVR, STD),
  los compila, mide cobertura real por test con gcov, ejecuta solo los tests
  que cubren la línea mutada y emite los ficheros raw de Major
  (`mutants.log`, `testMap.csv`, `covMap.csv`, `killMap.csv`, ...), de los que
  se construye el dataset. Ver [`builder4c/pmt/README.md`](builder4c/pmt/README.md).
- **`builder4c/projects_v1/`** — la definición de los proyectos C++
  (repositorio, commit, flags de CMake) y los ajustes por proyecto
  (`pmt_overrides.json`).
- **`builder4c/docker/`** — imagen y script para reproducir el batch completo
  en un contenedor desatendido. Guía detallada en
  [`builder4c/GUIA_UBUNTU.md`](builder4c/GUIA_UBUNTU.md).
- **`builder4c/demo/`** — proyecto C++ mínimo (lib + tests) para probar el
  pipeline de principio a fin en unos minutos.

## Reproducir el dataset

Requisitos: Docker (o un Linux con `build-essential cmake ninja-build git
python3` y las librerías de la guía). Sin dependencias Python.

```bash
cd builder4c

# 1) humo: demo end-to-end (~2 min) -> demo/out/Demo_1/Demo_1_results.csv
python3 -m pmt.mutation_runner --config demo/demo___mathlib/mutation.json --out demo/out/Demo_1/raw
python3 -m pmt.build_dataset --input demo/out/Demo_1/raw --project Demo --version 1 \
    --language cpp --src-root demo/demo___mathlib --out demo/out/Demo_1

# 2) batch sobre projects_v1 en Docker (horas; acotado por proyecto)
./docker/run_batch.sh --project-budget 1500 --max-mutants 60
tail -f out_batch_docker/progress.log

# 3) concatenar los CSV por proyecto en un unico dataset
python3 -m pmt.merge_datasets --out out_batch_docker
```

`--project-budget` limita los segundos totales por proyecto (clone + build +
cobertura + mutación) y `--max-mutants` el número de mutantes analizados;
sin ellos se aplican los presupuestos por fase de `pmt/batch_projects.py`.

## Validación del formato

El generador se verificó contra un dataset Java real producido con Major:
todas las columnas derivables coinciden al 100 % en sus 194 filas
(`python3 -m pmt.validate_reference`). Los datos de esa referencia no se
redistribuyen en este repositorio; las reglas del formato que se dedujeron
están documentadas en [`CLAUDE.md`](CLAUDE.md) y en `builder4c/pmt/README.md`.

## Estructura

```
dataset/                    dataset publicado (CSV) + reporte
builder4c/pmt/              pipeline (mutación, cobertura, dataset, batch, validación)
builder4c/projects_v1/      proyectos C++ objetivo (project.json, pmt_overrides.json)
builder4c/docker/           Dockerfile + run_batch.sh
builder4c/demo/             proyecto de demostración
builder4c/GUIA_UBUNTU.md    guía de ejecución (dependencias, presupuestos, limitaciones)
```

El dataset generado (`dataset/`), las salidas de ejecución (`builder4c/out_*`,
`builder4c/demo/out/`) y los datos de referencia Java (`Csv_1_*`) están
excluidos por `.gitignore`; el dataset se reproduce con los pasos anteriores.
