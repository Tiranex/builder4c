# pmt — Predictive Mutation Testing dataset para C++

Replica en C++ el pipeline de datasets PMT usado con proyectos Java
(herramienta **Major**), produciendo exactamente el mismo esquema de salida
que el dataset de referencia `Csv_1_results`.

## Flujo completo

```
proyecto C++ (CMake/Make + tests)
        │
        ▼
pmt.mutation_runner        (equivale a ejecutar Major)
        │  genera y ejecuta mutantes: compila cada mutante, corre solo los
        │  tests que cubren la linea mutada (cobertura real via gcov)
        ▼
raw estilo Major:  mutants.log · testMap.csv · covMap.csv · killMap.csv
                   kill.csv · summary.csv
        │
        ▼
pmt.build_dataset          (procesado intermedio + extraccion de fuentes)
        │
        ▼
[Proyecto]_[version]_results.csv    (una fila por mutante, 19 columnas)
[Proyecto]_[version]_test_map.csv   (TestMethod, TestMethodCode)
```

## Uso rapido (demo incluida)

Desde `builder4c/` en Linux (dev container, WSL o el contenedor de `../docker/`):

```bash
# 1. mutacion completa del proyecto demo (build, cobertura, mutantes)
python3 -m pmt.mutation_runner \
    --config demo/demo___mathlib/mutation.json \
    --out demo/out/Demo_1/raw

# 2. dataset final con el esquema del dataset Java
python3 -m pmt.build_dataset \
    --input demo/out/Demo_1/raw \
    --project Demo --version 1 --language cpp \
    --src-root demo/demo___mathlib \
    --out demo/out/Demo_1

# 3. (opcional) validar el pipeline contra el dataset Java de referencia
python3 -m pmt.validate_reference \
    --raw ../Csv_1_fixed_mutations/Csv_1_fixed \
    --ref-results ../Csv_1_results/Csv_1_results.csv \
    --ref-test-map ../Csv_1_results/Csv_1_test_map.csv
```

`build_dataset` tambien procesa directamente el raw Java de Major
(`--language java`), por lo que sirve para regenerar datasets de ambos
ecosistemas con un unico codigo.

## Equivalencias Java ⇄ C++

| Concepto Java (Major)            | Equivalente C++ (pmt)                        |
|----------------------------------|----------------------------------------------|
| clase `org.paquete.Clase`        | ruta del fichero: `src.calculator`           |
| test `Clase[metodo]`             | `tests.test_calculator[test_x]`              |
| cobertura instrumentada de Major | gcov (`--coverage`) por test                 |
| kill `FAIL`                      | test sale con codigo != 0 (assert)           |
| kill `EXC`                       | proceso muere por senal (abort/segv/sigfpe)  |
| kill `TIME`                      | timeout del runner                           |
| mutante no tipable (no generado) | mutante que no compila (descartado)          |

## Divergencias conocidas respecto al dataset de referencia

Verificado con `validate_reference` sobre las 194 filas de `Csv_1_results`:
todas las columnas derivables coinciden al 100 %. Diferencias documentadas:

- **Orden interno de `PassingTests`/`Tests`**: la referencia conserva el orden
  de ejecucion por mutante interno de Major (no derivable de los ficheros en
  bruto; hay ciclos entre filas). `pmt` ordena por `TestNo`. Los conjuntos son
  identicos; `Tests = KillingTests + PassingTests` se cumple en ambos.
- **Filas descartadas**: la referencia solo contiene mutantes cuyo metodo pudo
  extraer su herramienta de fuentes Java (194 de 612 cubiertos); `pmt` aplica
  la misma regla (descarta si la extraccion falla) pero su extractor no
  reproduce los fallos/truncamientos del extractor original.
- **Descriptores de tipo**: Major anota tipos (`==(int,int)`); en C++ textual
  no hay resolucion de tipos y se emite el operador sin tipos (`==`). Estos
  campos no forman parte del dataset final.

## Modulos

- `major_format.py` — E/S de los ficheros en bruto (formato Major exacto)
- `source_extract.py` — extraccion de metodos/funciones + tokenizador PMT
- `dataset.py` / `build_dataset.py` — dataset final (19 columnas + test_map)
- `cpp_mutator.py` — operadores ROR/AOR/COR/LVR/STD sobre C++ textual
- `coverage_gcov.py` — matriz test↔linea con gcov (`collect_coverage`: binario
  propio + `.gcov` de texto, demo; `collect_coverage_json`: proyectos
  CMake/ctest via `gcov --json-format`, incluye cabeceras; se activa con
  `"coverage": {"mode": "gcov_json", ...}` en `mutation.json`)
- `mutation_runner.py` — orquestador de la mutacion C++ (con cobertura:
  solo tests que cubren las fuentes, solo mutantes en lineas cubiertas,
  muestreo uniforme de candidatos hasta `--max-mutants`)
- `batch_projects.py` — batch sobre `projects_v1` (CMake/ctest + build
  instrumentado para cobertura; `--only`, `--project-budget`,
  `--max-mutants`); ver `../GUIA_UBUNTU.md` y `../docker/`
- `merge_datasets.py` — concatena los `<Proyecto>_1_results.csv` /
  `_test_map.csv` del batch en `pmt_dataset_results.csv` /
  `pmt_dataset_test_map.csv`
- `validate_reference.py` — verificacion contra el dataset Java publicado

Sin dependencias fuera de la libreria estandar de Python 3.8+.
