"""Mathematical anniversaries: days when your age in days is an interesting number.

Membership is a property of the integer, so everything here is computed live
from age_days and nothing is stored. That also means there is no upper bound to
worry about - the calendar lets a visitor browse any month, past or future, and
every predicate below is O(sqrt(n)) at worst.

Kept apart from core.matching, which relates people to events: an event carries
a name, a phrase, a date, tags and a person, and an anniversary carries none of
those. The two share no vocabulary and no data.

Each sequence is the enumeration of its POSITIVE terms. That single rule settles
both degenerate cases without a line of special-case code: day 0 (your birthday)
matches nothing, and day 1 matches seven of the eight because 1 really is the
first power of two, the first triangular number, and so on. Genuine coincidences
further up - 144 is both a perfect square and a Fibonacci number, 21 and 55 are
both Fibonacci and triangular - are reported as the several matches they are.
"""

from __future__ import annotations

from math import isqrt
from typing import Callable, Collection, Dict, List, Optional, Tuple

from core.config import SEQUENCE_TAXONOMY

_SUPERSCRIPT_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _superscript(n: int) -> str:
    """11 -> "¹¹". Exponents read better set as exponents in an editorial layout."""
    return str(n).translate(_SUPERSCRIPT_DIGITS)


def _ordinal(n: int) -> str:
    """1 -> "1st", 12 -> "12th", 21 -> "21st"."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# Each function below answers "is n in this sequence, and how do we describe it?"
# in one call, returning the description or None. Folding the predicate and the
# copy together means they cannot drift apart - there is no way to add a sequence
# whose membership test and whose printed index disagree.


def _powers_of_two(n: int) -> Optional[str]:
    if n < 1 or n & (n - 1):
        return None
    return f"a power of two, 2{_superscript(n.bit_length() - 1)}"


def _powers_of_ten(n: int) -> Optional[str]:
    if n < 1:
        return None
    remainder = n
    while remainder % 10 == 0:
        remainder //= 10
    if remainder != 1:
        return None
    return f"a power of ten, 10{_superscript(len(str(n)) - 1)}"


def _triangle_numbers(n: int) -> Optional[str]:
    if n < 1:
        return None
    index = (isqrt(8 * n + 1) - 1) // 2
    if index * (index + 1) // 2 != n:
        return None
    return f"the {_ordinal(index)} triangular number"


def _fibonacci_numbers(n: int) -> Optional[str]:
    if n < 1:
        return None
    current, following, index = 1, 1, 1
    while current < n:
        current, following = following, current + following
        index += 1
    if current != n:
        return None
    return f"the {_ordinal(index)} Fibonacci number"


def _primes(n: int) -> Optional[str]:
    if n < 2:
        return None
    if n % 2 == 0:
        return "a prime number" if n == 2 else None
    factor = 3
    while factor * factor <= n:
        if n % factor == 0:
            return None
        factor += 2
    return "a prime number"


def _perfect_squares(n: int) -> Optional[str]:
    if n < 1:
        return None
    root = isqrt(n)
    if root * root != n:
        return None
    return f"a perfect square, {root}{_superscript(2)}"


def _cubes(n: int) -> Optional[str]:
    if n < 1:
        return None
    # The float cube root drifts by more than a whole integer at this magnitude,
    # so the neighbours are checked rather than trusted.
    estimate = round(n ** (1 / 3))
    for root in (estimate - 1, estimate, estimate + 1):
        if root > 0 and root**3 == n:
            return f"a perfect cube, {root}{_superscript(3)}"
    return None


def _catalan_numbers(n: int) -> Optional[str]:
    if n < 1:
        return None
    # Enumerated 1-based over the DISTINCT terms 1, 2, 5, 14, 42, ... The
    # conventional C0 = 1 is skipped because it duplicates C1, which would make
    # every printed ordinal ambiguous.
    current, index, k = 1, 1, 1
    while current < n:
        k += 1
        current = current * 2 * (2 * k - 1) // (k + 1)
        index += 1
    if current != n:
        return None
    return f"the {_ordinal(index)} Catalan number"


#: Keyed by SEQUENCE_TAXONOMY name. A test asserts the keys match it exactly, so
#: adding a name without an implementation fails loudly rather than silently
#: producing a sequence nothing can ever match.
_SEQUENCES: Dict[str, Callable[[int], Optional[str]]] = {
    "Powers of 2": _powers_of_two,
    "Powers of 10": _powers_of_ten,
    "Triangle numbers": _triangle_numbers,
    "Fibonacci numbers": _fibonacci_numbers,
    "Primes": _primes,
    "Perfect squares": _perfect_squares,
    "Cubes": _cubes,
    "Catalan numbers": _catalan_numbers,
}


def sequences_for(age_days: int) -> List[Tuple[str, str]]:
    """(name, description) for every sequence age_days belongs to, in taxonomy order.

    Iterates the taxonomy rather than the dict so the order is guaranteed by the
    published constant, not by a dict literal someone might reorder.
    """
    found: List[Tuple[str, str]] = []
    for name in SEQUENCE_TAXONOMY:
        description = _SEQUENCES[name](age_days)
        if description is not None:
            found.append((name, description))
    return found


def anniversary_matches(
    age_days: int, included_sequences: Optional[Collection[str]]
) -> List[Dict]:
    """One match dict per included sequence age_days belongs to.

    The shared entry point for both the calendar and the daily cron job, so the
    day you see marked and the notification you receive can never disagree.
    A day in several sequences yields several matches; they are not merged.
    """
    included = set(included_sequences or ())
    return [
        {"sequence": name, "age_days": age_days, "description": description}
        for name, description in sequences_for(age_days)
        if name in included
    ]


def anniversary_sentence(match: Dict) -> str:
    """The display sentence for a match.

    Written fresh rather than routed through core.matching.full_sentence, which
    rebuilds an opening around an event's name and event_phrase - neither of
    which an anniversary has.
    """
    return f"Your age in days ({match['age_days']:,}) is {match['description']}."


