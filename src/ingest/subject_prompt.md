# Event subject-identification instructions

You will receive a JSON array of historical event records, each with:
- `text`: the raw event description (as scraped from Wikipedia's "on this day" data)
- `year`, `month`, `day`: the event's date
- `reason`: why this event needs you — `"unmatched"` (no known person was found in the
  text) or `"possible_reference"` (the text may only *reference* a person — e.g. something
  named after them, or an anniversary/memorial of them — rather than describe something
  they did)
- `candidates`: present only when a known person's name was found in the text — given as
  context, not as a suggestion to prefer over what you find yourself in `text`

For each record, return a JSON object with these fields:

- `text`: copied unchanged from the input. This is how the record is matched back, so it
  must be byte-identical to what you were given.
- `subject`: the name of the person who is the grammatical subject of `text` — the person
  the event is *about*, who performed or underwent the action. Rules:
  - Copy the name **verbatim as it appears in `text`**, and copy the person's **full name**
    as given, not a shortened piece of it. If the text says "Napoleon", return "Napoleon".
    If the text gives a longer form ("Quaid-i-Azam Muhammad Ali Jinnah"), return the full
    name as it appears — "Muhammad Ali Jinnah", not just "Muhammad Ali". Do not expand
    "Bach" into "Johann Sebastian Bach" or correct spelling — only use what's actually
    written in `text`.
  - **A person referenced, not acting, is not the subject.** If `text` describes something
    *named after* a person (a school, an award, a ship, a place), or describes an
    *anniversary* or *memorial* of a person's death or an earlier event, that person did not
    do anything on this date — return `null` for them, unless `text` separately, genuinely
    describes a different named person doing something on this date.
  - Return `null` if `text` names no person at all (a treaty, a battle between countries,
    an organization, a natural disaster), or if you cannot tell who the subject is. `null`
    is the correct, expected answer for many records — do not invent a person to avoid
    returning it.
  - Never return a name that does not literally appear in `text`.

Return a JSON array in the same order as the input, one object per input record. Return
every record you were given — don't skip any.
