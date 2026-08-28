# Nobel Prize event rewording instructions

You will receive a JSON array of Nobel Prize laureate records, each with:
- `laureate_id`, `award_year`: together identify this specific award (a repeat winner
  has one record per award, so these two fields together - not laureate_id alone -
  are what you must echo back unchanged for your output to be matched to the right
  record).
- `name`: the laureate's name.
- `category_display`: the prize's full display name, already correctly formatted
  (e.g. "the Nobel Prize in Physics", or "the Nobel Memorial Prize in Economic
  Sciences" for that one category) - use this exact string, don't reconstruct it
  from `category`.
- `motivation`: the official prize citation, e.g. "for his discovery of the neutron".

For each record, return a JSON object with these fields only:

- `laureate_id`: copied unchanged from the input.
- `award_year`: copied unchanged from the input.
- `name`: copied unchanged from the input.
- `event_phrase`: the sentence shown to the app user, written from the person onward.
  It always follows this template, with the `was when` hinge never varied:

  > **{person}** was when **{event}**.

  The app prepends a tensed opening in front of this ("You were the same age",
  "You're the same age", "You'll be the same age") - that part is computed by the
  app, not written by you. Begin your output directly with the person.

  - Default to `name` exactly as given. After first naming the person, use a
    pronoun rather than repeating the name - infer pronouns from the name only when
    genuinely unambiguous, otherwise use a short form of their name instead.
  - The event is always "won {category_display}" - reword the *justification*, not
    the fact of winning. Weave `motivation` in naturally rather than quoting it
    verbatim: "for his discovery of the neutron" becomes "...for discovering the
    neutron", not left in its citation phrasing.
  - Past tense throughout, matching "was when". End with a period.
  - One sentence. It renders as a bullet in a calendar dialog and as the body of a
    push notification - don't pad it with biographical detail `motivation` doesn't
    contain.
  - Preserve every concrete fact in `motivation`: what the achievement actually was,
    any named discovery/work/mechanism. Reword the structure, not the substance.

  Worked example - for `name` "Enrico Fermi", `category_display` "the Nobel Prize in
  Physics", `motivation` "for his demonstrations of the existence of new radioactive
  elements produced by neutron irradiation, and for his related discovery of nuclear
  reactions brought about by slow neutrons":

  > Enrico Fermi was when he won the Nobel Prize in Physics for demonstrating new
  > radioactive elements produced by neutron irradiation and discovering the nuclear
  > reactions caused by slow neutrons.

Return a JSON array in the same order as the input, one object per input record.
Return every record you were given - don't skip any, even if `event_phrase` ends up
close to a direct rewording of `motivation`.
