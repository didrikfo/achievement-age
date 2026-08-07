import json

from ingest.subject_extraction import build_prompt, prepare_subject_chunks


def test_build_prompt_describes_the_subject_field():
    prompt = build_prompt()
    assert "verbatim as it appears in `text`" in prompt
    assert "`subject`" in prompt


def test_prepare_subject_chunks_splits_by_chunk_size(tmp_path):
    pending_path = tmp_path / "subject_pending.json"
    pending_path.write_text(
        json.dumps([{"text": f"Event {index}", "reason": "unmatched"} for index in range(5)]),
        encoding="utf-8",
    )
    chunk_dir = tmp_path / "subject_chunks"

    paths = prepare_subject_chunks(pending_path=pending_path, chunk_size=2, chunk_dir=chunk_dir)

    assert len(paths) == 3
    assert paths[0].name == "chunk_0000.json"
    assert len(json.loads(paths[0].read_text(encoding="utf-8"))) == 2
    assert len(json.loads(paths[2].read_text(encoding="utf-8"))) == 1


def test_prepare_subject_chunks_handles_an_empty_queue(tmp_path):
    pending_path = tmp_path / "subject_pending.json"
    pending_path.write_text("[]", encoding="utf-8")

    paths = prepare_subject_chunks(pending_path=pending_path, chunk_dir=tmp_path / "chunks")

    assert paths == []
