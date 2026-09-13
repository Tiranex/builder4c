FROM mcr.microsoft.com/devcontainers/cpp:dev-ubuntu24.04

ARG arch=x86_64


RUN unset   CXX CC CXXFLAGS CFLAGS 

# Install newer cmake.
ENV CMAKE_VERSION 3.26.4
RUN apt-get update && apt-get install -y wget sudo && \
    wget -q https://github.com/Kitware/CMake/releases/download/v$CMAKE_VERSION/cmake-$CMAKE_VERSION-Linux-$arch.sh && \
    chmod +x cmake-$CMAKE_VERSION-Linux-$arch.sh && \
    ./cmake-$CMAKE_VERSION-Linux-$arch.sh --skip-license --prefix="/usr/local" && \
    rm cmake-$CMAKE_VERSION-Linux-$arch.sh


RUN apt-get update && \
    apt-get install -y \
        curl \
        wget \
        git \
        jq \
        patchelf \
        subversion \
        zip \
     build-essential make \
git binutils-dev zlib1g-dev \
texinfo bison flex g++-multilib  \
  vim \
  openssh-server \
  unzip \
  p7zip \
  ccache \
libtool \
automake \
  autoconf \
lsb-release \
software-properties-common gnupg\
  pkg-config \
ninja-build \
  python3 \
        libncurses5-dev \
        libgdbm-dev \
        libnss3-dev \
        libssl-dev \
        libsqlite3-dev \
        libreadline-dev \
        libffi-dev \
        libbz2-dev \
        liblzma-dev \
        python3-pip 


WORKDIR $SRC/
RUN wget -q https://apt.llvm.org/llvm.sh -O llvm.sh && chmod +x llvm.sh && ./llvm.sh 20 && rm llvm.sh && apt-get update && apt-get install -y clang-20 lldb-20 clangd-20

# RUN git clone -b llvmorg-16.0.0 --depth 1 https://github.com/llvm/llvm-project.git $SRC/llvm-project

# RUN   cmake -G Ninja \
#       -DCMAKE_BUILD_TYPE=Release \
#       -DLLVM_TARGETS_TO_BUILD="X86" \
#       -DLLVM_ENABLE_PROJECTS="clang;lld;" \
#       -DLLVM_ENABLE_RUNTIMES="libcxx;libcxxabi;compiler-rt" \
#       -DLLVM_BINUTILS_INCDIR="/usr/include/" \
#  -DLLVM_BUILD_TESTS=off \
#  -DLLVM_INCLUDE_TESTS=off \
#     -DCOMPILER_RT_INCLUDE_TESTS=OFF \
#       $SRC/llvm-project/llvm && \
#   ninja  &&  ninja  install && rm -fr $SRC/llvm-project 



# https://stackoverflow.com/questions/43878953/how-does-one-detect-if-one-is-running-within-a-docker-container-within-python
ENV AM_I_IN_A_DOCKER_CONTAINER="Yes"  CC="clang-20"  CXX="clang++-20" CCC="clang++-20" ARCHITECTURE="x86_64"


RUN    git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git /work/depot_tools.git && ln -s /work/depot_tools.git /work/depot_tools 


RUN apt-get install -y libgtest-dev