Unstructured collection of ideas for this project. Meant as a place to record possible features, changes, improvements, etc. for implementation later. Should be low bar to add items, and items should be removed or updated when (partially) implemented.

**Ideas**
- Add another type of calendar items, for processes with a certain duration. E.g. light distance to nearby cosmic objects, travel time for certain stretches at certain travel speeds. Possibly also a calendar item type for records, e.g. oldes recorded individual of different animal species.
- Add support for event tags, and a filtering system by tag, to include or exclude events in the calendar based on tags. *(Specced in `docs/superpowers/specs/2026-08-10-tag-filtering-design.md`, not yet implemented. The pipeline already assigns tags; the spec wires them through to a calendar filter and to per-subscriber notification preferences. Per-tag indicator colors were considered and dropped — a single red indicator stays.)*
- Change the name of the app to "Almanac of Me"
- Add another type of calendar item for "mathematical anniversary days", e.g. prime number of days, round multiple of 1000 days, powers of 2, maybe things like triangle numbers or other famous sequences
    * These should be filterable, both to include mathematical anniversaries or not, and by specific sequence (someone could keep prime days but filter out powers of 2 etc.)
    * These should have a different symbol in the calendar, e.g. a triangle instead of circle.