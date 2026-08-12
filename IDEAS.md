Unstructured collection of ideas for this project. Meant as a place to record possible features, changes, improvements, etc. for implementation later. Should be low bar to add items, and items should be removed or updated when (partially) implemented.

**Ideas**
- Add another type of calendar items, for processes with a certain duration. E.g. light distance to nearby cosmic objects, travel time for certain stretches at certain travel speeds. Possibly also a calendar item type for records, e.g. oldes recorded individual of different animal species.
- ~~Event tags and a filtering system~~ **Done.** Events carry 1–3 fine tags from a fixed
  20-name taxonomy, grouped into 8 coarse categories (`core.config.TAG_CATEGORIES`). The calendar
  filters on categories by default, with the fine tags behind an "Advanced" popover, and each
  subscriber's choice is carried into their daily notifications. Per-tag indicator colors were
  considered and dropped — a single red circle stays. Specs:
  `docs/superpowers/specs/2026-08-10-tag-filtering-design.md` and
  `docs/superpowers/specs/2026-08-11-wikipedia-links-and-tag-hierarchy-design.md`.
- Add Wikipedia links to *events* (not just the people), shown next to the person's link in the
  event dialog's "further reading" line. `app.links.further_reading_links` already returns a list
  for exactly this.
- Change the name of the app to "Almanac of Me"
- ~~Mathematical anniversary days~~ **Done.** Days when your age in days is itself an interesting
  number are marked with a triangle, computed live from `core.sequences` with no stored data. Eight
  sequences (`core.config.SEQUENCE_TAXONOMY`), individually selectable, with the whole feature
  opt-in via `subscriptions.included_sequences` — stored as inclusions, unlike the event filters, so
  it stays off for existing subscribers without a migration. Specs:
  `docs/superpowers/specs/2026-08-12-mathematical-anniversaries-design.md`.
- Let a subscriber track an arbitrary OEIS sequence by number, alongside the eight built-in ones.
  Needs its own design: an input and validation for the sequence id, a way to fetch and cache its
  terms, and per-subscriber sequence definitions rather than a fixed taxonomy.