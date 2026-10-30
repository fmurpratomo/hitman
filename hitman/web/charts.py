"""Arithmetic the report charts need and Jinja cannot do."""

from __future__ import annotations

import math


def nice_ceiling(value: float) -> float:
    """The smallest 1, 2 or 5 times a power of ten at or above ``value``.

    A y-axis topped at 500 reads at a glance; one topped at 437.4 does not.
    """
    if value <= 0:
        return 1.0
    power = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 5):
        if value <= step * power:
            return step * power
    return 10 * power
