CXX ?= g++
CXXFLAGS ?= -O0 -g -std=c++17

all: build/test_calculator

build/calculator.o: src/calculator.cpp src/calculator.h
	@mkdir -p build
	$(CXX) $(CXXFLAGS) -c src/calculator.cpp -o $@

build/test_calculator: build/calculator.o tests/test_calculator.cpp
	$(CXX) $(CXXFLAGS) build/calculator.o tests/test_calculator.cpp -o $@

# build instrumentado para la matriz de cobertura (gcov)
cov: build-cov/test_calculator

build-cov/calculator.o: src/calculator.cpp src/calculator.h
	@mkdir -p build-cov
	$(CXX) $(CXXFLAGS) --coverage -c src/calculator.cpp -o $@

build-cov/test_calculator: build-cov/calculator.o tests/test_calculator.cpp
	$(CXX) $(CXXFLAGS) --coverage build-cov/calculator.o tests/test_calculator.cpp -o $@

clean:
	rm -rf build build-cov

.PHONY: all cov clean
