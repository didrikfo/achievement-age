from ingest.backfill_music_wikipedia import plan_links, qids_by_person_name


def test_qids_by_person_name_uses_the_suffixed_person_for_a_namesake():
    records = [
        {"name": "Jack Johnson", "wikidata_qid": "Q297097"},
        {"name": "Prince", "wikidata_qid": "Q7542"},
        {"name": "Yoko Ono", "wikidata_qid": None},
    ]

    assert qids_by_person_name(records) == {"Jack Johnson (musician)": "Q297097", "Prince": "Q7542"}


def test_plan_links_links_by_qid_and_reports_an_item_with_no_english_article():
    persons = [{"id": 1, "name": "Prince"}, {"id": 2, "name": "Carola"}]
    qids = {"Prince": "Q7542", "Carola": "Q212524"}

    links, unlinked = plan_links(persons, qids, {"Q7542": "https://en.wikipedia.org/wiki/Prince_(musician)"})

    assert links == [(1, "https://en.wikipedia.org/wiki/Prince_(musician)")]
    assert unlinked == ["Carola"]


def test_plan_links_leaves_alone_a_person_who_is_not_a_resolved_musician():
    # The gangster keeps the plain name, so he must not pick up the rapper's article.
    persons = [{"id": 1194, "name": "Machine Gun Kelly"}]

    links, unlinked = plan_links(persons, {"Machine Gun Kelly (musician)": "Q1354843"}, {"Q1354843": "url"})

    assert (links, unlinked) == ([], [])
