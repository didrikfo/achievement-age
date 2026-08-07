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
- `event_phrase`: a rewording of `text` that reads as a natural continuation of the sentence
  "The same age that {name} was when ___." Rules:
  - Use a pronoun (he/she/they) instead of repeating `name`.
  - Keep it in past tense, matching "was when".
  - Preserve the original fact exactly — reword for flow, don't add or invent detail that
    isn't in `text`.
  - Strip any leftover Wikipedia-style artifacts from `text` (trailing citation brackets,
    "(b. ...)"/"(d. ...)" asides, etc.) that would read strangely mid-sentence.
  - Don't capitalize the first word unless it's a proper noun — it continues the sentence
    started by "The same age that {name} was when ".
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
