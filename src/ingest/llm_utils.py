"""LLM helpers used during data preparation."""

from __future__ import annotations

import os
from typing import Dict, List, Tuple

from dotenv import load_dotenv
from google import genai

from core.config import DATA_DIR
from core.io import load_json, parse_llm_output, save_to_json

load_dotenv(dotenv_path="dotenv.env")
API_KEY = os.getenv("PRIVATE_API_KEY")
client = genai.Client(api_key=API_KEY)

CHUNK_SIZE = 50
TMP_DIR = DATA_DIR / "tmp"
MODEL_NAME = "gemini-flash-latest"


def test_batch_job(client=client):  # pragma: no cover - helper used manually
    """Submit two toy prompts, primarily for verifying credentials."""
    inline_requests = [
        {
            "contents": [
                {
                    "parts": [{"text": "Tell me a one-sentence joke."}],
                    "role": "user",
                }
            ]
        },
        {
            "contents": [
                {"parts": [{"text": "Why is the sky blue?"}], "role": "user"}
            ]
        },
    ]

    inline_batch_job = client.batches.create(
        model="models/gemini-2.0-flash",
        src=inline_requests,
        config={"display_name": "inlined-requests-job-1"},
    )

    print(f"Created batch job: {inline_batch_job.name}")

INSTRUCTIONS = (
    "For each event record, add a display_text field of the form "
    "'The same age that {name} was when {event happened}'. "
    "Return ONLY a raw JSON array of the updated records: no markdown code fences, "
    "no commentary, no surrounding text."
)

STRICT_RETRY_SUFFIX = (
    " Your previous response was not valid JSON. Return strictly a JSON array of "
    "objects and nothing else - no code fences, no explanation."
)


def _event_key(event: Dict) -> Tuple[object, object]:
    """Natural key for an event: (name, text) - stable across pipeline reruns."""
    return (event.get("name"), event.get("text"))


def _fallback_display_text(event: Dict) -> str:
    """Deterministic display_text used when the LLM can't produce usable JSON."""
    text = event.get("text", "") or ""
    name = event.get("name", "")
    lowered = text[:1].lower() + text[1:] if text else text
    return f"The same age that {name} was when {lowered}"


def _call_llm(content: str, client=client):  # pragma: no cover - network call
    return client.models.generate_content(model=MODEL_NAME, contents=content)


def _process_chunk(chunk: List[Dict], chunk_index: int, client=client) -> List[Dict]:
    """Reword one chunk of events, retrying once and falling back to a template."""
    content = INSTRUCTIONS + str(chunk)
    response = _call_llm(content, client=client)

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    (TMP_DIR / f"chunk_{chunk_index:04d}_raw.txt").write_text(response.text, encoding="utf-8")

    try:
        return parse_llm_output(response.text)
    except ValueError:
        print(f"Chunk {chunk_index}: initial LLM output unparsable, retrying with stricter prompt.")
        retry_response = _call_llm(content + STRICT_RETRY_SUFFIX, client=client)
        (TMP_DIR / f"chunk_{chunk_index:04d}_retry.txt").write_text(retry_response.text, encoding="utf-8")
        try:
            return parse_llm_output(retry_response.text)
        except ValueError:
            print(f"Chunk {chunk_index}: retry also unparsable, using fallback template for this chunk.")
            return [{**event, "display_text": _fallback_display_text(event)} for event in chunk]


def get_pending_events(
    events_path=DATA_DIR / "events_with_age.json",
    displayable_path=DATA_DIR / "displayable_events.json",
) -> Tuple[List[Dict], List[Dict]]:
    """Return (already_processed, pending) events, split by whether display_text exists."""
    all_events = load_json(events_path)
    try:
        processed = load_json(displayable_path)
    except FileNotFoundError:
        processed = []

    processed_keys = {_event_key(event) for event in processed}
    pending = [event for event in all_events if _event_key(event) not in processed_keys]
    return processed, pending


def reword_event_descriptions(
    chunk_size: int = CHUNK_SIZE,
    max_events: int | None = None,
    client=client,
) -> List[Dict]:
    """Ensure every matched event has a display_text, resuming from prior progress.

    Already-processed events (present in displayable_events.json, matched by
    (name, text)) are skipped. Set max_events to cap how many pending events are
    sent to the LLM in this call (useful for a cheap smoke test).
    """
    processed, pending = get_pending_events()
    if max_events is not None:
        pending = pending[:max_events]

    if not pending:
        print("Nothing to process; all matched events already have display_text.")
        return processed

    print(f"{len(pending)} event(s) queued for this run ({len(processed)} already done).")

    results = list(processed)
    for chunk_index, start in enumerate(range(0, len(pending), chunk_size)):
        chunk = pending[start : start + chunk_size]
        print(f"Processing chunk {chunk_index} ({len(chunk)} events)...")
        chunk_results = _process_chunk(chunk, chunk_index, client=client)
        results.extend(chunk_results)
        save_to_json(DATA_DIR / "displayable_events.json", results)

    return results
