# Event rewording instructions

You will receive a JSON array of historical event records, each with:
- `id`: present for backfill-style processing of already-stored events (not present for
  local-JSON batches of newly matched events)
- `name`: the person currently matched to this event
- `text`: the raw event description (as scraped from Wikipedia's "on this day" data)
- `year`, `month`, `day`: the event's date
- `age`: the person's age in days at the time of the event (present for local-JSON batches;
  not present for Supabase backfill batches, which only have `year`/`month`/`day`)

For each record, return a JSON object with these fields:

- `id`: copied unchanged from the input (when the input record has one).
- `name`: copied unchanged from the input.
- `text`: copied unchanged from the input.
- `event_phrase`: the sentence shown to the app user, written from the person onward. It always
  follows this template, with the `was when` hinge never varied:

  > **{person}** was when **{event}**.

  The app prepends a tensed opening in front of this ("You were the same age", "You're the same
  age", "You'll be the same age", depending on whether the reader is viewing a day before, on, or
  after today) — that part is computed by the app, not written by you. Begin your output directly
  with the person.

  **Naming the person**
  - Default to `name` exactly as given.
  - If `text` attaches a title, rank, honorific, or established epithet to that person, include it
    here — "Sir Richard Owen", "Flight lieutenant Jerry Rawlings". Never invent one that isn't in
    `text`, and never substitute a different person.
  - After first naming the person, use a pronoun rather than repeating the name. If `text` doesn't
    establish the person's pronouns, use a short form of their name instead — don't guess.

  **Rewording**
  - Reword for how it *reads*, not to stay close to the original wording. Restructure clauses,
    reorder them, and split into more than one sentence whenever the original syntax is convoluted.
    Preserving the source's sentence structure is not a goal; preserving its meaning is.
  - If `text` doesn't already have `name` as its grammatical subject, restructure so they are — "he
    led the Prussian forces at the Battle of Kolín" rather than a clause-by-clause translation that
    leaves them stranded in a prepositional phrase.
  - Past tense throughout, matching "was when".
  - End with a period.
  - Keep it to roughly one to three sentences. It renders as a bullet in a calendar dialog and as the
    body of a push notification.

  **Fidelity**
  - Preserve every concrete fact: other people's names, places, organizations, numbers, and outcomes.
    Reword the structure, not the substance — don't add or invent detail that isn't in `text`.
  - If `text` opens with a framing prefix before a colon (`World War II:`, `Cuban Revolution:`), weave
    that context into the sentence rather than dropping it.
  - Strip Wikipedia-style artifacts from `text` (trailing citation brackets, "(b. ...)"/"(d. ...)"
    asides) that would read strangely in a finished sentence.

  Worked example — for `name` "Richard Owen" and `text` "A dinner party is held inside a life-size
  model of an iguanodon created by Benjamin Waterhouse Hawkins and Sir Richard Owen in south London,
  England.":

  > Sir Richard Owen was when a dinner party was held inside a life-size model of an iguanodon, which
  > he had created with Benjamin Waterhouse Hawkins, in south London, England.

  Note the title moved up next to the name, and the model's creation was recast so he is the one doing
  it. `name` in your output stays the bare "Richard Owen" — titles belong only in `event_phrase`.
- `tags`: an array of 1 to 3 tags from this fixed list only — do not invent new tags:
  {tags}
  Pick the tags that best describe what the event is about (e.g. a battle is `military`, a
  scientific paper is `science`, a court ruling is `law`).
- `suggested_subject`: usually `null`. Only set this if `text` mentions a *different* named
  person who is more clearly the grammatical subject of the sentence than `name` is (this
  happens when a description mentions multiple people and the wrong one got matched). If set,
  it must be a name copied verbatim as it appears in `text` — don't guess a full name that
  isn't actually written there.

Return a JSON array in the same order as the input, one object per input record. Return every
record you were given — don't skip any, even if `event_phrase` ends up being close to a direct
rewording of `text`.
