from core.matching import name_matches_text, normalize_name


def test_true_positive_with_punctuation():
    assert name_matches_text("O'Brien", "Kevin O'Brien announced the results.")


def test_false_positive_avoided_short_name_inside_longer_word():
    # A plain substring check would wrongly match "Art" inside "parts".
    assert not name_matches_text("Art", "He organized many parts of the exhibit.")


def test_diacritic_normalization():
    assert name_matches_text("José", "The news mentioned Jose Rizal today.")


def test_normalize_name_strips_diacritics_and_punctuation():
    assert normalize_name("José O'Brien") == "jose o brien"
