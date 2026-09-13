# CLAUDE.md — Dataset PMT para C++ (builder4c)

## Objetivo

Replicar en C++ el dataset de **Predictive Mutation Testing (PMT)** que se
genera en Java con la herramienta **Major**, tomando como oráculo el ejemplo
real incluido en el repo:

- Entrada raw (Major): `Csv_1_fixed_mutations/Csv_1_fixed/`
  (`mutants.log`, `testMap.csv`, `covMap.csv`, `killMap.csv`, `kill.csv`, `summary.csv`)
- Salida objetivo: `Csv_1_results/Csv_1_results.csv` (19 columnas, una fila
  por mutante) y `Csv_1_results/Csv_1_test_map.csv` (`TestMethod,TestMethodCode`)

## Qué se ha construido

Todo el pipeline vive en `builder4c/pmt/` (Python 3, solo librería estándar,
ejecutable en el dev container o en WSL Debian):

| Módulo | Función |
|---|---|
| `major_format.py` | E/S exacta de los ficheros raw estilo Major |
| `source_extract.py` | Extracción de métodos/funciones (Java y C++) + tokenizador PMT |
| `dataset.py` + `build_dataset.py` | Procesado intermedio → `_results.csv` + `_test_map.csv` |
| `cpp_mutator.py` | Operadores de Major sobre C++: ROR, AOR, COR, LVR, STD |
| `coverage_gcov.py` | Matriz test↔línea real con gcov (equivale a covMap) |
| `mutation_runner.py` | "Major para C++": genera/compila/ejecuta mutantes y emite el raw |
| `validate_reference.py` | Verificación automática contra el dataset Java publicado |

Proyecto de demostración C++ en `builder4c/demo/demo___mathlib/`
(lib + tests con granularidad JUnit: cada test se lista y ejecuta por nombre).

Documentación de uso: `builder4c/pmt/README.md`.

## Ingeniería inversa del formato (validada)

Reglas confirmadas ejecutando `pmt.validate_reference` contra las 194 filas
del dataset Java de referencia — **todas las columnas derivables coinciden al
100 %**:

- `Status`: `KILLED`/`SURVIVED`; `Label`: 1/0.
- Un mutante genera fila solo si está **cubierto** (covMap) y tiene método.
- `Tests = KillingTests + PassingTests` (concatenación literal).
- `PassingTests` = tests que cubren y no matan.
- `SrcMethodKey = {Clase}_{líneaFirma}_{últimaLíneaContenido}`;
  `MutSrcLineNo = Line − líneaFirma`.
- `SrcLines`/`TestMethodCode`: `repr()` de la lista de líneas del método
  **sin indentación y sin la llave de cierre**.
- `BeforePMT`/`AfterPMT`: tokens separados por `", "` con coma final
  (p. ej. `c, ==, '\n',`). Caso especial STD: `After = "NOOP"`,
  `AfterPMT = "<noop>,"`.
- `MatchingIdx`: siempre vacío en la referencia.
- Si la extracción de código de un método/test falla, la fila se descarta
  (así se explican las 194 de 612 filas de la referencia y los 52 de 54 tests).

Divergencias conocidas (documentadas en `pmt/README.md`): orden interno de
`PassingTests` (artefacto de ejecución de Major, con ciclos entre filas → no
derivable del raw; nosotros ordenamos por TestNo) y los truncamientos del
extractor Java original, que no se reproducen.

## Equivalencias Java → C++

| Java (Major) | C++ (pmt) |
|---|---|
| `org.paquete.Clase` | ruta del fichero: `src.calculator` |
| Test `Clase[método]` | `tests.test_calculator[test_x]` |
| Cobertura de Major | gcov (`--coverage`) ejecutando cada test por separado |
| Kill `FAIL` / `EXC` / `TIME` | exit≠0 / muerte por señal (abort, SIGFPE…) / timeout |
| Mutante no tipable (no se genera) | mutante que no compila (se descarta) |

## Cómo ejecutar

En Linux (dev container, WSL o el contenedor `builder4c/docker/`), desde
`builder4c/`:

```bash
# mutación completa del proyecto demo → raw estilo Major
python3 -m pmt.mutation_runner --config demo/demo___mathlib/mutation.json \
    --out demo/out/Demo_1/raw

# dataset final (mismo esquema que el de Java)
python3 -m pmt.build_dataset --input demo/out/Demo_1/raw \
    --project Demo --version 1 --language cpp \
    --src-root demo/demo___mathlib --out demo/out/Demo_1

# validación del pipeline contra la referencia Java (datos solo en local)
python3 -m pmt.validate_reference --raw ../Csv_1_fixed_mutations/Csv_1_fixed \
    --ref-results ../Csv_1_results/Csv_1_results.csv \
    --ref-test-map ../Csv_1_results/Csv_1_test_map.csv
```

Batch acotado en Docker y fusión (`dataset/` se copia desde `out_batch_docker/`):

```bash
./docker/run_batch.sh --project-budget 1500 --max-mutants 60 --only nanomsg___nng
python3 -m pmt.merge_datasets --out out_batch_docker
```

## Estado / progreso

- [x] **Repo publicable (2026-09-09)**: `builder4c/` (código `pmt/`,
      `projects_v1/`, `docker/`, `demo/`, guía) pasa a versionarse. El
      dataset final vive en **`dataset/`** (CSV concatenados, `report.md/json`
      y `projects/<P>_1/` con CSV + `raw/` por proyecto; ~2,4 MB) pero
      **se mantiene fuera de git** (`dataset/` en `.gitignore`, petición del
      2026-09-13); se regenera con el batch + `merge_datasets`. Siguen
      fuera también: datos Java de referencia `Csv_1_*` (no se
      redistribuyen), `defects4c/`, salidas `builder4c/out_*`,
      `builder4c/demo/out/`, logs y `anotaciones_*.yaml`. `.gitattributes`
      normaliza a LF; `pyproject.toml` sin dependencias, `uv.lock`
      regenerado; README raíz reescrito. El 2026-09-13 se eliminó el legado
      de la fase Defects4C (`cmake_builder.py`, `install_deps.sh`,
      `examples_usage.sh`, plantillas `.jinja`, `format_commit.py`,
      `bugs_list_new.json`): `projects_v1/` solo conserva `project.json` y
      `pmt_overrides.json`.
      Regla vigente: **nunca `git push`**; el usuario decide el commit.
- [x] Parser del raw Major + generador del dataset con el esquema exacto.
- [x] Validación contra la referencia Java: OK (194/194 filas, columnas
      derivables idénticas; ver arriba).
- [x] Motor de mutación C++ (ROR/AOR/COR/LVR/STD) con filtro de compilación.
- [x] Cobertura real por test con gcov → covMap.
- [x] Demo end-to-end en WSL Debian (ver resultados abajo).
- [x] Soporte para proyectos reales CMake/ctest en el runner (modo ctest:
      `-R` anclado, kills EXC/TIME desde los marcadores de ctest, descarte de
      tests rojos en línea base, deadline global y presupuesto por mutante).
- [x] Batch sobre los 17 proyectos de `projects_v1` (`pmt/batch_projects.py`):
      clone→configure→build→tests→mutación acotada→dataset, con verificación
      de que los ficheros mutados forman parte del build
      (compile_commands.json).
- [x] Primer intento de batch en WSL cancelado (v1, sin cobertura; el
      montaje /mnt/c no permitía crear directorios). Resultado v1 de
      `aws-c-common` (salida ya borrada): 36/36 vivos, porque covMap
      asumía que los 20 primeros tests cubrían todo.
- [x] **Cobertura gcov real en el batch (v2, 2026-09-02)**: segundo build
      instrumentado (`--coverage`), `gcov --json-format` por test
      (`coverage_gcov.collect_coverage_json`, funciona con objetos anidados
      de CMake y con cabeceras); el runner usa solo tests que cubren las
      fuentes, genera mutantes solo en líneas cubiertas y muestrea
      uniformemente hasta `MAX_MUTANTS` (100). aws-c-common: 31 mutantes,
      27 muertos, 4 vivos.
- [x] **Ejecución en Docker** (`builder4c/docker/`): imagen ubuntu:24.04 con
      las dependencias de la guía; `docker/run_batch.sh` lanza el batch
      desatendido (volumen `pmt_work` para clones/builds, resultados en
      `builder4c/out_batch_docker/`). Git necesita `http.version=HTTP/1.1`
      dentro de Docker Desktop. Validación contra la referencia Java y demo
      repetidas en el contenedor: OK.
- [x] `pmt/merge_datasets.py` concatena los CSV de todos los proyectos en
      `pmt_dataset_results.csv` + `pmt_dataset_test_map.csv`.
- [x] Batch en Docker (2026-09-02, **cancelado a petición** al llegar a
      SOCI, 12/25). Resultados en `builder4c/out_batch_docker/`:
      **7 proyectos OK, 304 filas** (147 KILLED / 157 SURVIVED; ROR 94,
      STD 93, LVR 65, AOR 40, COR 12) concatenadas en
      `pmt_dataset_results.csv` + `pmt_dataset_test_map.csv` (97 tests).
      Por proyecto: nng 78, aws-c-common 66, libevent 62, uncrustify 60,
      libzmq 17, cpp-sort 14, CLI11 7. Detalle en `report.md`.
      Fallos corregidos pero **pendientes de relanzar** con
      `./docker/run_batch.sh --only ...` (overrides en
      `projects_v1/<p>/pmt_overrides.json`, ver GUIA §10): rapidcheck
      (Catch2+glibc), libyang (tests solo en Debug), entt (cabeceras en
      `src/`), SPIRV-Tools (`git-sync-deps`). Sin procesar: SOCI, cppcheck,
      rocksdb, dynamorio, arrow, llvm y los 8 extra (Catch2, zstd, fmt,
      spdlog, googletest, leveldb, json, simdjson).
- [x] Bug corregido: firmas C++ con `::` rompían el formato `:` de
      `mutants.log` (escritor sanea `::`→`.`; parser tolerante para raws
      antiguos; validación Java sigue OK).
- [x] `batch_projects.py`: opciones `--project-budget S` (tope total por
      proyecto; acota clone/configure/build, build de cobertura ≤ ½ del
      restante, medición ≤ ⅓, mutación el resto) y `--max-mutants N`; el
      detalle del reporte ya no rompe la tabla markdown. `run_batch.sh` las
      reenvía.
- [x] **Segunda tanda (2026-09-09)** en Docker, 3 contenedores en paralelo
      (`--out out_run2_{a,b,c}`, luego fusionados en `out_batch_docker/`),
      `--project-budget 1500 --max-mutants 60`, ~1 h de reloj en total.
      OK: libyang 49 filas, Catch2 41 (override
      `-DCATCH_DEVELOPMENT_BUILD=ON` para que existan tests), fmt 40,
      leveldb 36, entt 16, SOCI 12, googletest 2, json 1 (cabecera incluida
      por toda la suite: un mutante ≈ recompilar todos los tests). Fallos
      documentados en `dataset/report.md`: rapidcheck y spdlog (ningún test
      cubre las fuentes elegidas), zstd (su único test que cubre,
      `playTests`, supera los 25 s y se descarta en línea base),
      SPIRV-Tools (configure; `git-sync-deps` no se ha reintentado).
      **Dataset total: 501 filas de 15 proyectos** (248 KILLED / 253
      SURVIVED; ROR 166, STD 150, LVR 95, AOR 63, COR 27), 177 tests.
      Sin procesar por coste: cppcheck, rocksdb, dynamorio, arrow, llvm,
      simdjson.
- [ ] libevent sale con 4/62 muertos porque sus tests `regress__*` superan
      los 25 s de `CTEST_TIMEOUT` y se descartan en línea base; relanzarlo
      exige subir también `MUTANT_BUDGET`/`MUTATION_DEADLINE`.
- [ ] `TestMethodCode` queda vacío (`[]`) para tests de ctest: no hay
      mapeo test→función fuente en proyectos reales (solo en la demo).
- [ ] Ampliar el dataset: relanzar json/googletest/SOCI con más presupuesto
      (`--project-budget 3600`), zstd con `ctest_timeout` alto en
      `pmt_overrides.json`, y los proyectos grandes con `--only`.
- [ ] Integración opcional con mull (`mull-runner`) como motor alternativo.

## Resultados de la demo (Demo_1)

Ejecutado en WSL Debian (g++ 14, gcov 14). Salidas en `builder4c/demo/out/Demo_1/`:

- **94 mutantes válidos** generados (0 descartados por no compilar),
  **83 cubiertos**, **70 muertos**, **13 vivos** (los 11 mutantes de `sign()`
  quedan sin cubrir a propósito: ninguna prueba la ejercita).
- Kills de los tres tipos, como en Major: `FAIL` (asserts), `EXC`
  (excepción sin capturar / SIGFPE en `safe_div` y `gcd`), `TIME`
  (bucle infinito al mutar `i <= to` → `true` en `sum_range`).
- `Demo_1_results.csv`: 83 filas × 19 columnas (esquema idéntico al Java);
  operadores: ROR 30, AOR 28, LVR 12, COR 8, STD 5.
  Supervivientes realistas, p. ej. `n % 2 == 0 → n % 2 <= 0` en `is_even`
  (solo se detecta con impares negativos, no testeados).
- `Demo_1_test_map.csv`: 11/11 tests con su código fuente extraído.

## Notas de entorno

- WSL: Ubuntu (por defecto) no tiene `cmake` ni librerías de desarrollo y
  `sudo` pide contraseña; Debian (parada) tiene `g++`, `make`, `gcov`,
  `python3` sin `cmake`/`ninja`. Para el batch usar Docker (imagen
  `pmt-batch`; el volumen `pmt_work` se borró el 2026-09-13 y se recrea
  solo al relanzar `run_batch.sh`, que vuelve a clonar y compilar).
- Limpieza 2026-09-13: eliminados `defects4c/`, `builder4c/out_batch/`,
  `out_run2_*`, `demo/out/`, logs y cachés; se conserva
  `builder4c/out_batch_docker/` como fuente de `dataset/`.
- El dev container (`.devcontainer/`) usa clang-20 (entorno de la fase
  previa; para el batch basta `builder4c/docker/`).
- Nunca hacer `git push` (regla del proyecto). Commits solo locales.
