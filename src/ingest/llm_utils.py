"""LLM helpers used during data preparation."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from google import genai

from core.io import load_json, parse_llm_output, save_to_json

load_dotenv(dotenv_path="dotenv.env")
API_KEY = os.getenv("PRIVATE_API_KEY")
client = genai.Client(api_key=API_KEY)


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


def reword_event_descriptions(client=client, data=None):  # pragma: no cover - manual helper
    """Use Gemini to produce short display text for events."""
    instructions = (
        "For each event record, add a display_text phrase of the form "
        "'The same age that {name} was when {event happened}'. Return the updated list "
        "as JSON without commentary."
    )

    data = data or load_json(os.path.join("data", "events_with_age.json"))[:100]
    content = instructions + str(data)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=content,
    )

    parsed_llm_output = parse_llm_output(response.text)
    save_to_json(os.path.join("data", "displayable_events.json"), parsed_llm_output)
    return parsed_llm_output
