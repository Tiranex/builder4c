#include "calculator.h"

#include <stdexcept>

bool is_even(int n) {
    return n % 2 == 0;
}

int abs_val(int v) {
    if (v < 0) {
        return -v;
    }
    return v;
}

int clamp(int value, int lo, int hi) {
    if (value < lo) {
        return lo;
    }
    if (value > hi) {
        return hi;
    }
    return value;
}

int gcd(int a, int b) {
    a = abs_val(a);
    b = abs_val(b);
    while (b != 0) {
        int t = a % b;
        a = b;
        b = t;
    }
    return a;
}

bool is_leap_year(int year) {
    return (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
}

int safe_div(int num, int den) {
    if (den == 0) {
        throw std::invalid_argument("division by zero");
    }
    return num / den;
}

int sum_range(int from, int to) {
    int total = 0;
    for (int i = from; i <= to; ++i) {
        total = total + i;
    }
    return total;
}

int sign(int v) {
    if (v > 0) {
        return 1;
    }
    if (v < 0) {
        return -1;
    }
    return 0;
}
