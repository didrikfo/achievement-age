import json

import pytest

from ingest import agent_batches
from ingest.agent_batches import (
    BatchConflict,
    batch_status,
    check_manifest,
    load_results,
    mark_merged,
    result_path_for,
    unmerged_chunks,
    write_chunks,
)

MANIFEST = {"kind": "reword", "prompt_version": 3}


def _records(count):
    return [{"name": f"Person {i}", "text": f"did thing {i}"} for i in range(count)]


def test_write_chunks_splits_records_and_writes_a_manifest(tmp_path):
    paths = write_chunks(tmp_path, _records(5), 2, MANIFEST)

    assert [path.name for path in paths] == ["chunk_0000.json", "chunk_0001.json", "chunk_0002.json"]
    manifest = json.loads((tmp_path / "batch.json").read_text(encoding="utf-8"))
    assert manifest == {**MANIFEST, "model": "opus", "chunks": 3}


def test_write_chunks_resumes_an_unmerged_batch_instead_of_rechunking(tmp_path):
    first = write_chunks(tmp_path, _records(4), 2, MANIFEST)
    mark_merged(first[0])

    resumed = write_chunks(tmp_path, _records(10), 2, MANIFEST)

    assert [path.name for path in resumed] == ["chunk_0001.json"]
    assert len(json.loads(resumed[0].read_text(encoding="utf-8"))) == 2


def test_write_chunks_refuses_to_resume_a_different_batch(tmp_path):
    write_chunks(tmp_path, _records(2), 2, {"kind": "enrichment", "mode": "tags", "prompt_version": 3})

    with pytest.raises(BatchConflict):
        write_chunks(tmp_path, _records(2), 2, {"kind": "enrichment", "mode": "phrasing", "prompt_version": 3})


def test_write_chunks_fresh_discards_old_chunks_and_stale_results(tmp_path):
    old = write_chunks(tmp_path, _records(6), 2, MANIFEST)
    result_path_for(old[0]).write_text("[]", encoding="utf-8")
    mark_merged(old[1])

    paths = write_chunks(tmp_path, _records(1), 2, MANIFEST, fresh=True)

    assert [path.name for path in paths] == ["chunk_0000.json"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["batch.json", "chunk_0000.json"]


def test_write_chunks_starts_clean_once_everything_is_merged(tmp_path):
    for path in write_chunks(tmp_path, _records(2), 1, MANIFEST):
        result_path_for(path).write_text("[]", encoding="utf-8")
        mark_merged(path)

    paths = write_chunks(tmp_path, _records(1), 1, MANIFEST)

    assert [path.name for path in paths] == ["chunk_0000.json"]
    assert not (tmp_path / "merged").exists()


def test_load_results_treats_missing_truncated_and_misshapen_files_as_nothing(tmp_path):
    path = tmp_path / "result.json"
    assert load_results(path) is None
    path.write_text('[{"name": "Ada", "text": "wro', encoding="utf-8")
    assert load_results(path) is None
    path.write_text('{"name": "Ada"}', encoding="utf-8")
    assert load_results(path) is None
    path.write_text('["just a string"]', encoding="utf-8")
    assert load_results(path) is None
    path.write_text('[{"name": "Ada"}, 42]', encoding="utf-8")
    assert load_results(path) == [{"name": "Ada"}]


def test_check_manifest_refuses_output_from_an_older_prompt_version(tmp_path):
    [chunk] = write_chunks(tmp_path, _records(1), 5, {"kind": "reword", "prompt_version": 2})

    with pytest.raises(BatchConflict):
        check_manifest(chunk, "reword", 3)
    check_manifest(chunk, "reword", 2)
    check_manifest(tmp_path / "elsewhere" / "chunk_0000.json", "reword", 3)  # no manifest: passes


def test_batch_status_sorts_chunks_by_what_they_need_next(tmp_path):
    chunks = write_chunks(tmp_path, _records(4), 1, MANIFEST)
    result_path_for(chunks[1]).write_text('[{"name": "Person 1"}]', encoding="utf-8")
    result_path_for(chunks[2]).write_text('[{"name": "Pers', encoding="utf-8")  # cut off mid-write
    result_path_for(chunks[3]).write_text('[{"name": "Person 3"}]', encoding="utf-8")
    mark_merged(chunks[3])

    status = batch_status(tmp_path)

    assert status["awaiting_agent"] == ["chunk_0000.json", "chunk_0002.json"]
    assert status["ready_to_merge"] == ["chunk_0001.json"]
    assert status["merged"] == 1
    assert [path.name for path in unmerged_chunks(tmp_path)] == ["chunk_0000.json", "chunk_0001.json", "chunk_0002.json"]


def test_merge_ready_merges_only_chunks_with_valid_results(tmp_path, monkeypatch):
    chunks = write_chunks(tmp_path, _records(2), 1, {"kind": "enrichment", "mode": "phrasing", "prompt_version": 3})
    result_path_for(chunks[1]).write_text('[{"name": "Person 1"}]', encoding="utf-8")
    calls = []

    def fake_merge(chunk, result, manifest):
        calls.append((chunk.name, result.name, manifest["mode"]))
        mark_merged(chunk, result)

    kind = agent_batches.BatchKind("enrichment", lambda: tmp_path, "prompt()", fake_merge)
    monkeypatch.setattr(agent_batches, "_kinds", lambda: {"enrichment": kind})

    assert agent_batches.merge_ready("enrichment") == ["chunk_0001.json"]
    assert calls == [("chunk_0001.json", "chunk_0001_result.json", "phrasing")]
    assert batch_status(tmp_path)["awaiting_agent"] == ["chunk_0000.json"]
