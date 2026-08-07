Unstructured collection of ideas for this project. Meant as a place to record possible features, changes, improvements, etc. for implementation later. Should be low bar to add items, and items should be removed or updated when (partially) implemented.

**Ideas**
- Add another type of calendar items, for processes with a certain duration. E.g. light distance to nearby cosmic objects, travel time for certain stretches at certain travel speeds. Possibly also a calendar item type for records, e.g. oldes recorded individual of different animal species.
- Add link to the webapp in the notification that is sent, so we can one tap open it (not sure if that works in ntfy).
- Improve and expand LLM processing of events:
    * Assign apropriate tags (TBD if a single event should have multiple tags)
    * Check whether the meatched name is actually the apropriate subject for the given event, sometimes another name would be more apropriate as the subject, in cases with muitiple names mentioned in a single event description
    * Add a instruction to make sure that the generated text is not strange, and reads nicely.
- Add support for event tags, and a filtering system by tag, to include or exclude events in the calendar based on tags. Consider changing the color of the calendar indicator based on tag, although the result might get too busy with lots of tags, so keeping a single color (red) might be best.
- Change the name of the app to "Almanac of Me"
- Add another type of calendar item for "mathematical anniversary days", e.g. prime number of days, round multiple of 1000 days, powers of 2, maybe things like triangle numbers or other famous sequences
    * These should be filterable, both to include mathematical anniversaries or not, and by specific sequence (someone could keep prime days but filter out powers of 2 etc.)
    * These should have a different symbol in the calendar, e.g. a triangle instead of circle.