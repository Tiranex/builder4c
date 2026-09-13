# Imagen para ejecutar el batch PMT (pmt/batch_projects.py) sobre projects_v1.
# Equivale a la seccion 2 de GUIA_UBUNTU.md.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake ninja-build git python3 pkg-config ca-certificates \
    libssl-dev libpcre2-dev libpcre3-dev zlib1g-dev libsodium-dev \
    libcmocka-dev libgtest-dev libgmock-dev libbenchmark-dev libevent-dev \
    libboost-all-dev libgflags-dev libsnappy-dev liblz4-dev libzstd-dev libbz2-dev \
    libsqlite3-dev libpq-dev default-libmysqlclient-dev unixodbc-dev \
    autoconf automake libtool bison flex \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work/builder4c
CMD ["bash"]
# git con HTTP/2 falla en Docker Desktop (Windows): "expected flush after ref listing"
RUN git config --system http.version HTTP/1.1
