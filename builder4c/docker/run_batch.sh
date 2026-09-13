#!/usr/bin/env bash
# Lanza el batch PMT completo en Docker (contenedor desatendido "pmt-batch").
#   ./docker/run_batch.sh            # todos los proyectos de projects_v1
#   ./docker/run_batch.sh --only CLIUtils___CLI11 --only nanomsg___nng
# Seguimiento:  tail -f out_batch_docker/progress.log
# Resultados:   out_batch_docker/<Proyecto>_1/*.csv, report.md, report.json
set -euo pipefail
export MSYS_NO_PATHCONV=1
HERE="$(cd "$(dirname "$0")/.." && pwd)"
HOST_DIR="${HOST_DIR:-$(cygpath -m "$HERE" 2>/dev/null || echo "$HERE")}"
OUT="${OUT:-out_batch_docker}"
docker build -q -t pmt-batch "$HOST_DIR/docker" >/dev/null
docker rm -f pmt-batch >/dev/null 2>&1 || true
docker volume create pmt_work >/dev/null
docker run -d --name pmt-batch \
    -v "$HOST_DIR:/work/builder4c" -v pmt_work:/pmt_work -w /work/builder4c \
    pmt-batch bash -c "python3 -m pmt.batch_projects --projects projects_v1 \
        --work /pmt_work --out $OUT $* > $OUT.log 2>&1"
echo "contenedor pmt-batch lanzado; progreso en $OUT/progress.log"
