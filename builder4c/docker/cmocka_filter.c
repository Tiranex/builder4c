/*
 * Shim LD_PRELOAD para ejecutar UN caso de un binario cmocka.
 *
 * La cmocka 1.1.7 de Ubuntu 24.04 no lee ninguna variable de entorno de
 * filtro: solo expone cmocka_set_test_filter(). Este shim intercepta
 * _cmocka_run_group_tests (a lo que expande la macro cmocka_run_group_tests)
 * y, si existe CMOCKA_TEST_FILTER, fija el filtro antes de ejecutar el grupo.
 *
 *   LD_PRELOAD=/usr/local/lib/libcmocka_filter.so \
 *   CMOCKA_TEST_FILTER=test_schema_yang ./utest_int8
 *
 * Lo usa pmt.test_source_map (test_expansion = "cmocka").
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stddef.h>
#include <stdlib.h>

void cmocka_set_test_filter(const char *pattern);

typedef int (*run_group_fn)(const char *, const void *, size_t, void *, void *);

int _cmocka_run_group_tests(const char *group_name, const void *tests,
                            size_t num_tests, void *group_setup,
                            void *group_teardown)
{
    static run_group_fn real = NULL;
    const char *filter = getenv("CMOCKA_TEST_FILTER");

    if (real == NULL) {
        real = (run_group_fn)dlsym(RTLD_NEXT, "_cmocka_run_group_tests");
    }
    if (filter != NULL && filter[0] != '\0') {
        cmocka_set_test_filter(filter);
    }
    return real(group_name, tests, num_tests, group_setup, group_teardown);
}
