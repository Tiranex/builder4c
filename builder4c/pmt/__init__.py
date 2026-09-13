"""pmt - Predictive Mutation Testing dataset tooling for builder4c.

Replica en C++ el pipeline de datasets PMT usado en Java (herramienta Major):

    raw (mutants.log, testMap.csv, covMap.csv, killMap.csv)
        -> [proyecto]_[version]_results.csv   (una fila por mutante)
        -> [proyecto]_[version]_test_map.csv  (una fila por caso de prueba)

Modulos:
    major_format    lectura/escritura de los ficheros en bruto estilo Major
    source_extract  extraccion de metodos/funciones (Java y C++) y tokenizador PMT
    dataset         construccion de las filas del dataset final
    build_dataset   CLI: raw + codigo fuente -> dataset final
    cpp_mutator     generacion de mutantes para C++ (ROR/AOR/COR/LVR/STD)
    coverage_gcov   matriz de cobertura test<->linea via gcov
    mutation_runner CLI: ejecuta la mutacion sobre un proyecto C++ y emite el raw
"""

__version__ = "0.1.0"
