from core.config import DEFAULT_SEQUENCES, SEQUENCE_TAXONOMY
from core.sequences import (
    _ordinal,
    _SEQUENCES,
    _superscript,
    anniversary_matches,
    anniversary_sentence,
    included_sequences_for_subscription,
    sequences_for,
)


def _names(age_days):
    return [name for name, _ in sequences_for(age_days)]


def _description(age_days, sequence):
    return dict(sequences_for(age_days))[sequence]


# --- taxonomy wiring ---


def test_every_taxonomy_name_has_exactly_one_implementation():
    assert list(_SEQUENCES) == SEQUENCE_TAXONOMY


def test_the_default_set_is_the_sparse_legible_four():
    assert DEFAULT_SEQUENCES == [
        "Powers of 2",
        "Powers of 10",
        "Triangle numbers",
        "Fibonacci numbers",
    ]


def test_primes_are_never_on_by_default():
    # ~1 in 9 days is prime near a 90-year lifespan, so defaulting these on
    # would turn a handful-of-times-a-year feature into a weekly push.
    assert "Primes" not in DEFAULT_SEQUENCES
    assert set(DEFAULT_SEQUENCES).issubset(SEQUENCE_TAXONOMY)


def test_sequences_for_returns_taxonomy_order_not_discovery_order():
    # 1 hits almost everything; the result must still read in taxonomy order.
    found = _names(1)
    assert found == [name for name in SEQUENCE_TAXONOMY if name in set(found)]


# --- per-sequence membership ---


def test_powers_of_two():
    assert "Powers of 2" in _names(2048)
    assert "Powers of 2" in _names(32768)
    assert "Powers of 2" not in _names(2047)
    assert "Powers of 2" not in _names(32767)
    assert _description(2048, "Powers of 2") == "a power of two, 2¹¹"


def test_powers_of_ten():
    assert "Powers of 10" in _names(10000)
    assert "Powers of 10" not in _names(20000)
    assert "Powers of 10" not in _names(10001)
    assert _description(10000, "Powers of 10") == "a power of ten, 10⁴"


def test_triangle_numbers():
    assert "Triangle numbers" in _names(5050)
    assert "Triangle numbers" not in _names(5051)
    assert _description(5050, "Triangle numbers") == "the 100th triangular number"


def test_fibonacci_numbers():
    assert "Fibonacci numbers" in _names(4181)
    assert "Fibonacci numbers" in _names(28657)
    assert "Fibonacci numbers" not in _names(4180)
    assert _description(4181, "Fibonacci numbers") == "the 19th Fibonacci number"


def test_primes():
    assert "Primes" in _names(10007)
    assert "Primes" in _names(2)
    assert "Primes" not in _names(10008)
    assert "Primes" not in _names(32767)  # 7 * 31 * 151
    assert _description(10007, "Primes") == "a prime number"


def test_perfect_squares():
    assert "Perfect squares" in _names(10000)
    assert "Perfect squares" not in _names(10001)
    assert _description(10000, "Perfect squares") == "a perfect square, 100²"


def test_cubes():
    assert "Cubes" in _names(8000)
    assert "Cubes" not in _names(8001)
    # Guards the float cube root, which drifts at this magnitude.
    assert "Cubes" in _names(32768)
    assert _description(8000, "Cubes") == "a perfect cube, 20³"


def test_catalan_numbers():
    assert "Catalan numbers" in _names(4862)
    assert "Catalan numbers" not in _names(4863)
    # Enumerated 1-based over distinct terms (1, 2, 5, 14, ...), skipping the
    # conventional C0 = 1 which duplicates C1.
    assert _description(4862, "Catalan numbers") == "the 9th Catalan number"
    assert _description(2, "Catalan numbers") == "the 2nd Catalan number"


# --- the degenerate days, left exactly as they fall out ---


def test_day_zero_matches_nothing():
    # Your birthday. No sequence's positive enumeration contains 0, so this
    # needs no special case - and must not acquire one.
    assert sequences_for(0) == []


def test_negative_days_match_nothing():
    assert sequences_for(-1) == []
    assert sequences_for(-10000) == []


def test_day_one_matches_seven_of_eight():
    # 1 genuinely is the first power of two, power of ten, triangular number,
    # Fibonacci number, square, cube and Catalan number. Only prime is out.
    found = _names(1)
    assert set(found) == set(SEQUENCE_TAXONOMY) - {"Primes"}


# --- coincidences between sequences, deliberately not deduplicated ---


def test_day_144_is_both_a_square_and_a_fibonacci_number():
    # The largest number that is both, by Cohn's theorem. Both must be reported.
    found = _names(144)
    assert "Perfect squares" in found
    assert "Fibonacci numbers" in found


def test_days_21_and_55_are_both_fibonacci_and_triangular():
    for age_days in (21, 55):
        found = _names(age_days)
        assert "Fibonacci numbers" in found
        assert "Triangle numbers" in found


# --- anniversary_matches ---


def test_anniversary_matches_keeps_only_included_sequences():
    matches = anniversary_matches(144, ["Fibonacci numbers"])
    assert [match["sequence"] for match in matches] == ["Fibonacci numbers"]
    assert matches[0]["age_days"] == 144


def test_anniversary_matches_is_empty_when_nothing_is_included():
    assert anniversary_matches(2048, []) == []
    assert anniversary_matches(2048, None) == []


def test_anniversary_matches_ignores_names_outside_the_taxonomy():
    assert anniversary_matches(2048, ["Not A Sequence"]) == []


def test_anniversary_matches_returns_one_entry_per_matching_sequence():
    matches = anniversary_matches(1, SEQUENCE_TAXONOMY)
    assert len(matches) == 7


# --- copy ---


def test_anniversary_sentence_reads_as_a_full_sentence_with_a_grouped_number():
    match = anniversary_matches(2048, ["Powers of 2"])[0]
    assert anniversary_sentence(match) == "Your age in days (2,048) is a power of two, 2¹¹."


def test_ordinal_handles_the_teens():
    assert [_ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 100)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "100th"
    ]


def test_superscript_handles_multiple_digits():
    assert _superscript(11) == "¹¹"
    assert _superscript(2) == "²"


# --- subscription reader ---


def test_included_sequences_for_subscription_reads_the_stored_list():
    subscription = {"included_sequences": ["Primes", "Powers of 2"]}
    # Ordered by the taxonomy, not by how they were stored.
    assert included_sequences_for_subscription(subscription) == ["Powers of 2", "Primes"]


def test_included_sequences_for_subscription_survives_a_missing_column():
    # Before the alter-table lands, every subscription row is missing the key.
    # Raising here would kill the whole daily run; and "nothing" is also the
    # correct default, so this path is safe in both directions.
    assert included_sequences_for_subscription({}) == []


def test_included_sequences_for_subscription_treats_null_as_nothing():
    assert included_sequences_for_subscription({"included_sequences": None}) == []


def test_included_sequences_for_subscription_ignores_unknown_names():
    subscription = {"included_sequences": ["Powers of 2", "Retired Sequence"]}
    assert included_sequences_for_subscription(subscription) == ["Powers of 2"]
