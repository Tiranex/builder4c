# Guía: generar el dataset PMT de projects_v1 en una máquina Ubuntu

Cómo ejecutar el batch de mutación (`pmt/batch_projects.py`) sobre los 17
proyectos de `projects_v1` en Ubuntu (probado el pipeline en Debian 13 con
g++ 14; cualquier Ubuntu 22.04/24.04 sirve).

## 1. Qué copiar a la máquina

Clona el repositorio (o copia el directorio `builder4c/` completo). Lo
imprescindible:

```
builder4c/
├── pmt/            # el pipeline completo (Python 3, solo stdlib)
├── projects_v1/    # project.json de cada proyecto (repo, commit, flags)
└── demo/           # opcional: proyecto de humo para verificar la instalación
```

Opcional para la validación contra la referencia Java: `Csv_1_fixed_mutations/`
y `Csv_1_results/` (no están en el repositorio; van fuera de `builder4c/`,
cópialos al lado).

## 2. Dependencias

```bash
sudo apt update
sudo apt install -y build-essential cmake ninja-build git python3 pkg-config \
    libssl-dev libpcre2-dev libpcre3-dev zlib1g-dev libsodium-dev \
    libcmocka-dev libgtest-dev libbenchmark-dev libevent-dev \
    autoconf automake libtool
```

No hace falta ningún paquete de Python: `pmt` usa solo la librería estándar.
`gcov` viene con gcc.

## 3. Verificación rápida (5 minutos)

```bash
cd ~/builder4c

# demo end-to-end: mutación + dataset (debe acabar con ~94 mutantes)
python3 -m pmt.mutation_runner --config demo/demo___mathlib/mutation.json \
    --out demo/out/Demo_1/raw
python3 -m pmt.build_dataset --input demo/out/Demo_1/raw \
    --project Demo --version 1 --language cpp \
    --src-root demo/demo___mathlib --out demo/out/Demo_1

# opcional, si copiaste los datos Csv_1: validación contra la referencia Java
python3 -m pmt.validate_reference --raw ../Csv_1_fixed_mutations/Csv_1_fixed \
    --ref-results ../Csv_1_results/Csv_1_results.csv \
    --ref-test-map ../Csv_1_results/Csv_1_test_map.csv
# debe terminar con "RESULTADO: OK"
```

## 4. Lanzar el batch completo

El batch tarda **horas** (clona, compila y muta 17 proyectos): lánzalo en
`tmux`/`screen` o con `nohup`. Usa disco local rápido; reserva ≥ 50 GB.

```bash
cd ~/builder4c
nohup python3 -m pmt.batch_projects \
    --projects projects_v1 \
    --work ~/pmt_batch \
    --out ~/pmt_out > ~/pmt_out.log 2>&1 &

# seguimiento
tail -f ~/pmt_out/progress.log
```

- `--work`: clones y builds (lo pesado; bórralo al acabar).
- `--out`: resultados — un directorio `<Proyecto>_1/` por proyecto con el
  `raw/` estilo Major, `<Proyecto>_1_results.csv` y `<Proyecto>_1_test_map.csv`,
  más `report.md` / `report.json` (se reescriben tras cada proyecto, así que
  puedes consultarlos a mitad de ejecución) y `progress.log`.
- `--only NOMBRE` (repetible) procesa solo esos proyectos, p. ej.
  `--only zeromq___libzmq --only CLIUtils___CLI11`.
- Re-lanzar es barato: los checkouts existentes en `--work` se reutilizan.

## 5. Presupuestos y ajustes

Los límites viven como constantes al principio de `pmt/batch_projects.py`:

| Constante | Valor | Significado |
|---|---|---|
| `CLONE_TIMEOUT` | 600 s | clone superficial (al commit del project.json) |
| `CONFIGURE_TIMEOUT` / `BUILD_TIMEOUT` | 600 / 1500 s | cmake / ninja |
| `MAX_TESTS` | 20 | tests de ctest usados (los rojos en línea base se descartan) |
| `MAX_MUTANTS` | 100 | mutantes procesados por proyecto (muestreo uniforme entre los candidatos cubiertos) |
| `MUTATION_DEADLINE` | 1500 s | tope de la fase de análisis (corta y deja parcial) |
| `MUTANT_BUDGET` | 240 s | tope por mutante (si se excede, se descarta entero) |
| `CTEST_TIMEOUT` | 25 s | timeout por test (los TIME salen de aquí) |
| `COVERAGE_BUILD_TIMEOUT` | 1500 s | build instrumentado (`--coverage`) |
| `COVERAGE_MAX_TESTS` / `COVERAGE_DEADLINE` | 150 / 900 s | tests medidos con gcov y tope de esa fase |

Para un dataset más grande sube `MAX_MUTANTS`/`MAX_TESTS`/`MUTATION_DEADLINE`
(el coste crece ~linealmente con ambos).

Dos opciones de línea de comandos acotan el batch sin tocar las constantes
(también se pueden pasar a `docker/run_batch.sh`):

| Opción | Efecto |
|---|---|
| `--project-budget S` | tope de `S` segundos por proyecto para clone + configure + build + cobertura + mutación. Clone/configure/build reciben `min(tope de fase, restante)`; el build de cobertura como mucho la mitad de lo restante y su medición un tercio; la mutación lo que quede (mínimo 120 s). Un proyecto que agota el presupuesto queda como `FALLO` o con dataset parcial, pero nunca bloquea el batch. |
| `--max-mutants N` | mutantes analizados por proyecto (defecto `MAX_MUTANTS` = 100) |

El dataset publicado en `dataset/` se generó con `--project-budget 1500
--max-mutants 60` para los proyectos añadidos en la segunda tanda (ver
`dataset/README.md`).

## 6. Qué esperar de cada proyecto

- **Deberían funcionar**: aws-c-common (verificado end-to-end), CLI11,
  rapidcheck, cpp-sort, nng, libevent, libzmq, libyang, entt, uncrustify.
- **Pueden fallar por dependencias**: SOCI (backends de BD), SPIRV-Tools
  (necesita `utils/git-sync-deps` para SPIRV-Headers), libyang si falta
  `libpcre2-dev`.
- **Fallarán por presupuesto casi seguro**: arrow, rocksdb, llvm-project,
  dynamorio, cppcheck quizá (build > 25 min). Es intencional: aparecen en el
  reporte como `FALLO en build (timeout)`. Súbeles el presupuesto y lánzalos
  con `--only` si los necesitas.
- **fmtlib___fmt** no tiene `project.json` → sale como `FALLO en config`.

## 7. Limitaciones conocidas del batch (v1)

- **Cobertura gcov real (desde v2)**: el batch configura un segundo build
  `<work>/<proyecto>/build-cov` con `--coverage -O0`, mide con
  `gcov --json-format` qué líneas de los ficheros a mutar ejecuta cada test
  (hasta `COVERAGE_MAX_TESTS` = 150 tests, priorizando los que mencionan el
  fichero en su nombre, con tope `COVERAGE_DEADLINE` = 900 s), y a partir de
  ahí: (1) solo se usan como tests los que cubren alguna fuente, ordenados
  por líneas cubiertas y recortados a `MAX_TESTS`; (2) solo se generan
  mutantes en líneas cubiertas; (3) los candidatos se muestrean de forma
  uniforme a lo largo de ficheros y líneas hasta `MAX_MUTANTS`. Con esto
  aws-c-common pasó de 36/36 vivos (v1, sin cobertura) a 31 mutantes con
  27 muertos y 4 vivos. Si el build instrumentado falla, el proyecto sigue
  sin cobertura (se anota en `progress.log`) y covMap asume que los 20
  primeros tests cubren todo, como en v1.
- La selección de fuentes es heurística (tamaño medio, excluye tests/vendor,
  verificada contra `compile_commands.json`). Para control fino, edita
  `sources` en el `mutation.json` generado.
- Kills vía ctest: `FAIL`/`EXC`/`TIME` se deducen de los marcadores de ctest
  (`***Exception`/`***Timeout`).

## 8. Resultado final

Cada `<Proyecto>_1_results.csv` tiene exactamente el mismo esquema de 19
columnas que el dataset Java de referencia (`Csv_1_results.csv`), validado
al 100 % en las columnas derivables — se pueden concatenar los CSV de todos
los proyectos para el dataset grande de entrenamiento.

## 9. Alternativa: ejecutar el batch en Docker (Windows/macOS/Linux)

No hace falta una máquina Ubuntu: `docker/Dockerfile` construye una imagen
`ubuntu:24.04` con las dependencias de la sección 2 y `docker/run_batch.sh`
lanza el batch en un contenedor desatendido.

```bash
cd builder4c
./docker/run_batch.sh                       # todos los proyectos
./docker/run_batch.sh --only nanomsg___nng  # solo algunos (repetible)
tail -f out_batch_docker/progress.log       # seguimiento
docker logs -f pmt-batch                    # idem, desde el contenedor
```

- Clones y builds van a un volumen Docker (`pmt_work`, rápido; se reutiliza
  entre ejecuciones y se borra con `docker volume rm pmt_work`).
- Los resultados se escriben en `builder4c/out_batch_docker/` (bind mount):
  `<Proyecto>_1/`, `report.md`, `report.json`, `progress.log`.
- `docker rm -f pmt-batch` detiene el batch; relanzar reutiliza los checkouts.
- La imagen fija `http.version=HTTP/1.1` en git: con HTTP/2, git dentro de
  Docker Desktop falla con `expected flush after ref listing`.

## 10. Ajustes por proyecto (`pmt_overrides.json`)

Un fichero opcional `projects_v1/<proyecto>/pmt_overrides.json` permite
corregir proyectos concretos sin tocar `project.json`:

```json
{
  "pre_configure": ["python3 utils/git-sync-deps"],
  "cmake_flags": ["-DENABLE_BUILD_TESTS=ON"],
  "c_flags": "", "cxx_flags": "-DCATCH_CONFIG_NO_POSIX_SIGNALS",
  "ctest_timeout": 25
}
```

Ya incluidos: `emil-e___rapidcheck` (Catch2 v2 + glibc moderna),
`CESNET___libyang` (tests solo en Debug por defecto) y
`KhronosGroup___SPIRV-Tools` (dependencias en `external/`). Relanzar solo
esos: `./docker/run_batch.sh --only emil-e___rapidcheck --only CESNET___libyang
--only skypjack___entt --only KhronosGroup___SPIRV-Tools` (el reporte se
fusiona con el existente).

Desde 2026-09 el mismo fichero también amplía los límites del batch por
proyecto (tienen prioridad sobre `--max-mutants` y los valores por defecto):

```json
{
  "sources": ["source/hash_table.c", "source/uri.c"],
  "max_sources": 3,
  "max_tests": 0,
  "max_mutants": 1500,
  "mutant_budget_s": 600,
  "mutation_deadline_s": 14400,
  "coverage_max_tests": 600,
  "coverage_deadline_s": 2400,
  "test_expansion": "cmocka",
  "exclude_tests": "_valgrind$"
}
```

- `sources`: ficheros a mutar (relativos al repo); sin ella, selección
  heurística de `max_sources` ficheros (excluye `*_test.c` y similares).
- `max_tests`: tests que cubren las fuentes que se usan (0 = todos).
- `max_mutants`, `mutant_budget_s`, `mutation_deadline_s`: mutantes
  muestreados, presupuesto por mutante y tope de la fase de mutación.
- `coverage_max_tests`, `coverage_deadline_s`: tests medidos con gcov y tope
  de esa fase.
- `test_expansion`: `"cmocka"`, `"gtest"` o `"catch2"` para convertir cada
  binario de tests en sus casos individuales (ver `pmt/README.md`, «Enlace
  test ctest → codigo»); `expand_tests`: regex de los tests de ctest que se
  expanden (Catch2: `^RunTests$`). `exclude_tests`: regex de tests de ctest
  a descartar.
- `test_workers`: tests de un mismo mutante en paralelo (4 en la tanda 3);
  todo test que falla en paralelo se reejecuta solo y ese es su veredicto.
- `timeout_factor` / `timeout_floor_s` (10 / 3 por defecto): limite por test
  durante la mutacion = `min(ctest_timeout, max(floor, factor × t_base + 2))`,
  como en Major; evita que los mutantes con bucle infinito agoten el
  presupuesto y se pierdan los kills `TIME`.

Ejemplos completos en `projects_v1/{awslabs___aws-c-common,CESNET___libyang,
catchorg___Catch2}/pmt_overrides.json` y plan de ejecución en
`HANDOFF_ampliacion_3_proyectos.md`. `--smoke` recorre todo el pipeline con
límites mínimos para probar la configuración antes de la tanda larga.
Con varios contenedores en paralelo, usar `NAME=` y `OUT=` distintos:
`NAME=pmt-aws OUT=out_run3_aws ./docker/run_batch.sh --only awslabs___aws-c-common`.
