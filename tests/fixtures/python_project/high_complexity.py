"""A deliberately tangled function to trigger Radon's cyclomatic-complexity and maintainability findings."""


def classify(a, b, c, d, e, f):  # noqa: too many branches, on purpose
    result = 0
    if a > 0:
        result += 1
    else:
        result -= 1
    if b > 0:
        result += 1
    else:
        result -= 1
    if c > 0:
        result += 1
    else:
        result -= 1
    if d > 0:
        result += 1
    else:
        result -= 1
    if e > 0:
        result += 1
    else:
        result -= 1
    if f > 0:
        result += 1
    else:
        result -= 1
    if a > b:
        result += 1
    if b > c:
        result += 1
    if c > d:
        result += 1
    if d > e:
        result += 1
    if e > f:
        result += 1
    if a + b > c + d:
        result += 1
    return result
