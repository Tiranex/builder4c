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
  `_test_map.csv` / `_test_sources.csv` del batch en `pmt_dataset_results.csv`
  / `pmt_dataset_test_map.csv` / `pmt_dataset_test_sources.csv` y reune los
  `_meta.json` (commit por proyecto) en `pmt_dataset_meta.json`
- `test_source_map.py` — enlace test de ctest → codigo del caso de prueba
  (ver siguiente seccion)
- `validate_reference.py` — verificacion contra el dataset Java publicado
- `verify_dataset.py` — verificacion estatica y por reejecucion de un
  dataset generado (ver seccion propia)
- `publish_dataset.py` — monta el `dataset/` publicable a partir de las
  salidas del batch

Sin dependencias fuera de la libreria estandar de Python 3.8+.
Tests unitarios: `python3 -m unittest discover -s tests_pmt` (desde `builder4c/`).

## Enlace test ctest → codigo del caso de prueba

En los proyectos reales el "test" es cada entrada de `ctest -N`
(`ctest.<Proyecto>.<nombre ctest>` en el dataset). `test_source_map.py`
resuelve donde vive su codigo combinando `ctest --show-only=json-v1`
(comando y argumentos de cada test) con un indice de los ficheros de test
del repo:

| Estrategia | Cuando | Granularidad |
|---|---|---|
| `aws_test_case` | `driver <nombre>` y `AWS_TEST_CASE(<nombre>, fn)` | funcion `fn` |
| `catch_test_case` | un argumento coincide con `TEST_CASE("...")` / `SCENARIO` / `TEMPLATE_TEST_CASE` | ese bloque |
| `test_macro` | macro propia con cuerpo `XXX_TEST_CASE(nombre) {` | ese bloque |
| `gtest_filter` | `--gtest_filter=Suite.Name` o nombre `Suite.Name` | `TEST(Suite, Name)` |
| `subtest` | id expandido `<test>/<funcion>` (ver abajo) | esa funcion |
| `script` | comando python/bash con un fichero del repo | fichero |
| `aws_test_macro` | test generado por una macro con `##` (`DEFINE_*_TEST(fn, ...)`) | invocacion + `#define` expandido + funciones argumento (`macro`) |
| `exe_stem` | ejecutable `utest_x` / `x_test` (o un argumento: `regress_fuzz_<x>`) ↔ fichero de test | fichero |
| `ctest_definition` | test sin caso concreto (Catch2 `--list-tests`, `-h`...) | `add_test(...)` + `set_tests_properties(...)` del CMakeLists (`ctest`) |
| `none` | sin correspondencia | — |

Salidas: `raw/test_sources.csv` (TestNo, fichero, linea, funcion, granularidad,
estrategia, comando), `raw/ctest_tests.json` (json-v1 crudo) y
`raw/meta.json` (repo, **commit**, fuentes mutadas, limites). `build_dataset`
usa `test_sources.csv` para rellenar `TestMethodCode` (misma convencion que
el dataset Java: lineas sin indentacion, desde la linea del nombre, sin la
llave final) y escribe `<P>_<v>_test_sources.csv` con una columna
`SourceUrl` = enlace permanente `repo/blob/<commit>/<fichero>#L<linea>`.

**Expansion en sub-tests** (`test_expansion` en `mutation.json` /
`pmt_overrides.json`): cuando un test de ctest es un binario con muchos
casos, cada caso pasa a ser un test del dataset (`ctest.<P>.<test>/<caso>`)
con enlace a su funcion:

| Modo | Listado de casos | Ejecucion de un caso |
|---|---|---|
| `cmocka` | funciones `UTEST()` / `cmocka_unit_test*()` del fichero del binario | `LD_PRELOAD=libcmocka_filter.so CMOCKA_TEST_FILTER=<fn> ctest -R ^<test>$` |
| `gtest` | `<exe> --gtest_list_tests` | `GTEST_FILTER=Suite.Name ctest -R ^<test>$` |
| `catch2` | `<exe> --list-tests --verbosity high -r xml` (trae fichero y linea) | `cd <wd> && exec timeout -k 5 T <exe> "<nombre escapado>"` (sin ctest) |

- La cmocka 1.1.7 de Ubuntu **no** lee ninguna variable de filtro: la imagen
  Docker compila `docker/cmocka_filter.c`, un shim `LD_PRELOAD` que llama a
  `cmocka_set_test_filter()` con `$CMOCKA_TEST_FILTER` (ruta configurable con
  `PMT_CMOCKA_PRELOAD`).
- En `catch2` el binario se llama directamente (ctest no puede pasarle el
  filtro); el timeout de coreutils devuelve 124 (`TIME`) y la muerte por
  senal ≥ 128 (`EXC`). En la fase de cobertura se usa el binario de
  `build-cov`.
- `expand_tests` (regex) limita que tests de ctest se expanden (Catch2:
  `^RunTests$`, la suite completa); `exclude_tests` (regex) descarta tests
  de ctest antes de todo.
- Granularidad `section`: cuando un `add_test` de Catch2 lanza
  `SelfTest "<caso>" -c <seccion> [-c <sub>]`, `TestMethodCode` contiene el
  `TEST_CASE` sin las `SECTION` hermanas que no se ejecutan (columna
  `Sections` de `test_sources.csv`).

Ejecucion de los tests de cada mutante (`mutation.json`): `test_workers`
en paralelo; cada test que falla en paralelo se reejecuta solo y ese es su
veredicto (interferencias entre tests, p. ej. `utest_inout/test_output_fd`
de libyang, no generan kills falsos). Limite por test =
`min(timeout_s, max(timeout_floor_s, timeout_factor × t_base + 2))` con
`t_base` medido en la linea base (`raw/test_timeouts.csv`); si mas de 3
tests agotan el tiempo, se confirman 3 a solas y, si repiten, se aceptan
todos (bucle infinito sistematico).

`pmt.test_source_map --relink [--expansion catch2 --expand-only ^RunTests$]`
vuelve a enlazar un raw ya generado con el indice actual sin empeorar
nunca un enlace previo; despues `pmt.build_dataset` regenera los CSV.
`pmt.publish_dataset` monta `dataset/` (copia de proyectos, `source_snapshot/`
con los fuentes mutados y los ficheros de test en el commit, CSV
concatenados y reporte).

El runner guarda ademas `raw/mutant_locations.csv` (offsets exactos de cada
mutante: `mutants.log`, como en Major, solo guarda la linea) y procesa los
mutantes muestreados en orden aleatorio determinista cuando hay deadline,
para que un corte por tiempo no deje fuera los ultimos ficheros.

## Verificacion del dataset (`verify_dataset.py`)

```bash
python3 -m pmt.verify_dataset --projects projects_v1 --replay 25     --project-dir out_run3_aws/aws-c-common_1 --repo /pmt_work/awslabs___aws-c-common/repo
```

- **Estatica**: recalcula con codigo independiente todo lo derivable del raw
  y del fuente en el commit (Status/Label/Killing/Passing/Tests contra
  killMap/covMap, SrcMethodKey, MutSrcLineNo, Body, SrcLines, Before/After,
  BeforePMT/AfterPMT, tests existentes, test_sources con fichero/linea
  validos, commit = `commit_hash` de `project.json`).
- **Dinamica** (`--replay N`, dentro del contenedor): reaplica N mutantes
  `KILLED` y N `SURVIVED` en sus offsets exactos, recompila y reejecuta sus
  tests: los `KILLED` deben volver a fallar y los `SURVIVED` pasar.

Uso manual sobre un build ya configurado:

```bash
python3 -m pmt.test_source_map --build-dir /pmt_work/X/build \
    --repo /pmt_work/X/repo --tests-from out/X_1/raw/testMap.csv --out out/X_1/raw
```
