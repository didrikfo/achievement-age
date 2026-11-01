from scripts.audit_event_phrases import audit, old_display_doubled_hinge


def _row(id_, name, phrase, source="initial_migration", version=2):
    return {"id": id_, "name": name, "text": "", "event_phrase": phrase, "source": source, "reword_prompt_version": version}


def test_old_display_doubled_hinge_catches_punctuation_after_the_hinge():
    row = _row(1, "Louis XIV", "King Louis XIV of France was when, in Dover, he signed a treaty.")
    assert old_display_doubled_hinge(row)


def test_old_display_doubled_hinge_catches_a_different_name_form():
    assert old_display_doubled_hinge(_row(1, "Louis XIV", "King Louis of France was when he signed a treaty."))


def test_old_display_doubled_hinge_ignores_well_formed_and_suffix_only_rows():
    assert not old_display_doubled_hinge(_row(1, "Ada Lovelace", "Ada Lovelace was when she wrote."))
    assert not old_display_doubled_hinge(_row(2, "Ada Lovelace", "she wrote"))
    assert not old_display_doubled_hinge(_row(3, "Ada Lovelace", "The same age that Ada Lovelace was when she wrote."))


def test_audit_counts_the_backpass_and_skips_structured_sources():
    rows = [
        _row(1, "Louis XIV", "King Louis XIV of France was when, in Dover, he signed a treaty."),
        _row(2, "Ada Lovelace", "Ada Lovelace was when she wrote."),
        _row(3, "Rod Stewart", 'Rod Stewart was when "Maggie May" reached number one.', source="billboard_hot_100_number_ones", version=0),
    ]
    report = audit(rows)
    assert report["backpass_pending"] == 2
    assert [entry["id"] for entry in report["doubled_hinge_under_old_display"]] == [1]
    assert [entry["id"] for entry in report["format_failures"]] == [1]
