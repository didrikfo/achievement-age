# Event subject-identification instructions

You will receive a JSON array of historical event records, each with:
- `text`: the raw event description (as scraped from Wikipedia's "on this day" data)
- `year`, `month`, `day`: the event's date
- `reason`: why this event needs you — `"unmatched"` (no known person was found in the
  text) or `"ambiguous"` (several known people were found and we need the right one)
- `candidates`: present only when `reason` is `"ambiguous"` — the names that were found

For each record, return a JSON object with these fields:

- `text`: copied unchanged from the input. This is how the record is matched back, so it
  must be byte-identical to what you were given.
- `subject`: the name of the single person who is the grammatical subject of `text` — the
  person the event is *about*, who performed or underwent the action. Rules:
  - Copy the name **verbatim as it appears in `text`**. Do not expand "Bach" into "Johann
    Sebastian Bach", and do not correct spelling. If the text says "Napoleon", return
    "Napoleon".
  - When `reason` is `"ambiguous"`, prefer whichever of `candidates` is the true subject,
    but if the real subject is a different person named in `text`, return that name instead.
  - Return `null` if `text` names no person at all (a treaty, a battle between countries,
    an organization, a natural disaster), or if you cannot tell which person is the subject.
    `null` is the correct, expected answer for many records — do not invent a person to
    avoid returning it.
  - Never return a name that does not literally appear in `text`.

Return a JSON array in the same order as the input, one object per input record. Return
every record you were given — don't skip any.
