// Mini framework de tests con la granularidad de JUnit (un caso = un metodo)
// para reproducir el pipeline PMT: el runner ejecuta cada test por separado.
//
//   ./test_calculator --list      lista los nombres de los tests
//   ./test_calculator <nombre>    ejecuta un test: exit 0 pasa, exit 1 falla
//
// Las excepciones no capturadas terminan el proceso con SIGABRT (EXC) y los
// bucles infinitos se cortan por timeout en el runner (TIME).

#include "../src/calculator.h"

#include <cstdio>
#include <cstring>
#include <stdexcept>

static int failures = 0;

#define EXPECT_EQ(expected, actual)                                        \
    do {                                                                   \
        if ((expected) != (actual)) {                                      \
            std::printf("FAIL %s:%d: expected %s\n", __FILE__, __LINE__,   \
                        #expected " == " #actual);                         \
            ++failures;                                                    \
        }                                                                  \
    } while (0)

#define EXPECT_TRUE(cond) EXPECT_EQ(true, static_cast<bool>(cond))
#define EXPECT_FALSE(cond) EXPECT_EQ(false, static_cast<bool>(cond))

static void test_is_even_basic() {
    EXPECT_TRUE(is_even(4));
    EXPECT_FALSE(is_even(3));
    EXPECT_TRUE(is_even(0));
}

static void test_abs_val() {
    EXPECT_EQ(5, abs_val(5));
    EXPECT_EQ(3, abs_val(-3));
    EXPECT_EQ(0, abs_val(0));
}

static void test_clamp_low() {
    EXPECT_EQ(2, clamp(1, 2, 5));
}

static void test_clamp_high() {
    EXPECT_EQ(5, clamp(9, 2, 5));
}

static void test_clamp_middle() {
    EXPECT_EQ(3, clamp(3, 2, 5));
}

static void test_gcd_basic() {
    EXPECT_EQ(6, gcd(12, 18));
    EXPECT_EQ(1, gcd(7, 3));
}

static void test_gcd_with_zero() {
    EXPECT_EQ(5, gcd(0, 5));
    EXPECT_EQ(5, gcd(5, 0));
}

static void test_leap_year() {
    EXPECT_TRUE(is_leap_year(2000));
    EXPECT_FALSE(is_leap_year(1900));
    EXPECT_TRUE(is_leap_year(2024));
    EXPECT_FALSE(is_leap_year(2023));
}

static void test_safe_div_basic() {
    EXPECT_EQ(5, safe_div(10, 2));
    EXPECT_EQ(-2, safe_div(4, -2));
}

static void test_safe_div_by_zero_throws() {
    bool thrown = false;
    try {
        safe_div(1, 0);
    } catch (const std::invalid_argument&) {
        thrown = true;
    }
    EXPECT_TRUE(thrown);
}

static void test_sum_range_basic() {
    EXPECT_EQ(10, sum_range(1, 4));
    EXPECT_EQ(5, sum_range(5, 5));
}

struct TestCase {
    const char* name;
    void (*fn)();
};

static const TestCase kTests[] = {
    {"test_is_even_basic", test_is_even_basic},
    {"test_abs_val", test_abs_val},
    {"test_clamp_low", test_clamp_low},
    {"test_clamp_high", test_clamp_high},
    {"test_clamp_middle", test_clamp_middle},
    {"test_gcd_basic", test_gcd_basic},
    {"test_gcd_with_zero", test_gcd_with_zero},
    {"test_leap_year", test_leap_year},
    {"test_safe_div_basic", test_safe_div_basic},
    {"test_safe_div_by_zero_throws", test_safe_div_by_zero_throws},
    {"test_sum_range_basic", test_sum_range_basic},
};

int main(int argc, char** argv) {
    const int total = sizeof(kTests) / sizeof(kTests[0]);
    if (argc == 2 && std::strcmp(argv[1], "--list") == 0) {
        for (int i = 0; i < total; ++i) {
            std::printf("%s\n", kTests[i].name);
        }
        return 0;
    }
    if (argc == 2) {
        for (int i = 0; i < total; ++i) {
            if (std::strcmp(argv[1], kTests[i].name) == 0) {
                kTests[i].fn();
                return failures == 0 ? 0 : 1;
            }
        }
        std::printf("test desconocido: %s\n", argv[1]);
        return 2;
    }
    for (int i = 0; i < total; ++i) {
        kTests[i].fn();
    }
    std::printf("%s (%d fallos)\n", failures == 0 ? "OK" : "FAILED", failures);
    return failures == 0 ? 0 : 1;
}
