"""`ReplayArtifact` field-list and stub-honesty tests.

per docs/tasks/m9-phase-1-replay-storage-skeleton.md §1 / §9.

asserts the load-bearing field list is exactly what the brief
specifies — no `pydantic_schema_hash`, no `replay_artifact_ref` cycle,
no embedded `Extraction`, no embedded `InterviewTranscript`.
"""

from __future__ import annotations

import msgspec
import pytest

from extractx.replay import (
    CURRENT_REPLAY_ARTIFACT_SCHEMA_VERSION,
    REPLAY_ARTIFACT_SCHEMA_V1,
    REPLAY_ARTIFACT_SCHEMA_V2,
    REPLAY_ARTIFACT_SCHEMA_V3,
    SUPPORTED_REPLAY_ARTIFACT_SCHEMA_VERSIONS,
    ReplayArtifactReader,
    ReplayArtifactWriter,
    read_replay,
)
from extractx.replay.artifact import ReplayArtifact


def test_replay_artifact_field_list_is_canonical() -> None:
    """the artifact carries exactly the canonical fields and no others."""

    expected = {
        "schema_version",
        "extractx_version",
        "source_ref",
        "document_id",
        "spec_version",
        "strategy",
        "outcome",
        "producer_versions",
        "policy_summary",
        "runtime_bindings_summary",
        "candidate_sets",
        "instance_candidate_set",
        "instance_proposer_response",
        "instance_proposer_metadata",
        "observations",
        "selector_call_diagnostics",
        "validated_fields",
        "pre_resolver_negatives",
        "final_instances",
        "usage_events",
        "trace",
    }
    assert set(ReplayArtifact.model_fields.keys()) == expected


def test_replay_artifact_no_pydantic_schema_hash() -> None:
    """`pydantic_schema_hash` is NOT a field on `ReplayArtifact`
    (M9 phase-1 hard pin #6); duplicating `spec_version` is forbidden."""

    assert "pydantic_schema_hash" not in ReplayArtifact.model_fields


def test_replay_artifact_no_replay_artifact_ref() -> None:
    """the artifact does NOT carry its own id (would cycle with
    `Extraction.replay_artifact_ref`)."""

    assert "replay_artifact_ref" not in ReplayArtifact.model_fields


def test_replay_artifact_no_extraction_field() -> None:
    """no embedded `Extraction` (reconstruction composes from the
    listed canonical fields)."""

    forbidden = {"result", "extraction_result", "instances_result"}
    assert forbidden.isdisjoint(ReplayArtifact.model_fields.keys())


def test_replay_artifact_no_interview_transcript() -> None:
    """no `InterviewTranscript` slot — anti-pattern §15
    `Transcripts-In-Default-Replay-Artifact`."""

    forbidden = {"interview", "interview_transcript", "interview_transcripts"}
    assert forbidden.isdisjoint(ReplayArtifact.model_fields.keys())


def test_replay_artifact_is_frozen() -> None:
    """`ReplayArtifact` is a frozen pydantic `BaseModel`."""

    assert ReplayArtifact.model_config.get("frozen") is True
    assert ReplayArtifact.model_config.get("extra") == "forbid"


def test_replay_artifact_has_no_to_pydantic_method() -> None:
    """no `to_pydantic` method on the artifact; reconstruction is a
    top-level helper, not a method (M9 phase-1 brief §9 stub honesty
    clause)."""

    assert not hasattr(ReplayArtifact, "to_pydantic")


def test_replay_artifact_has_no_to_extraction_result_method() -> None:
    """no method that returns an `Extraction` directly."""

    assert not hasattr(ReplayArtifact, "to_extraction_result")


def test_replay_schema_versions_current_writes_are_v3() -> None:
    assert CURRENT_REPLAY_ARTIFACT_SCHEMA_VERSION == REPLAY_ARTIFACT_SCHEMA_V3
    assert REPLAY_ARTIFACT_SCHEMA_V3 in SUPPORTED_REPLAY_ARTIFACT_SCHEMA_VERSIONS
    assert REPLAY_ARTIFACT_SCHEMA_V2 in SUPPORTED_REPLAY_ARTIFACT_SCHEMA_VERSIONS


@pytest.mark.asyncio
async def test_current_writer_emits_v3_observations_and_selector_diagnostics(
    executor_with_storage,
    pydantic_spec,
    runtime,
    policy,
    doc_complete: str,
    store,
) -> None:
    result = await executor_with_storage.execute(
        document=doc_complete,
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )

    blob = store.get_object("replay", result.replay_artifact_ref)
    payload = msgspec.msgpack.decode(blob)

    assert isinstance(payload, dict)
    assert payload["schema_version"] == REPLAY_ARTIFACT_SCHEMA_V3
    assert "observations" in payload
    assert "selector_call_diagnostics" in payload
    assert "selections" not in payload
    artifact = ReplayArtifactReader().deserialize(blob)
    assert artifact.observations != ()
    assert artifact.selector_call_diagnostics != ()


@pytest.mark.asyncio
async def test_replay_selector_call_diagnostics_reference_presented_candidates(
    executor_with_storage,
    pydantic_spec,
    runtime,
    policy,
    doc_complete: str,
    store,
) -> None:
    result = await executor_with_storage.execute(
        document=doc_complete,
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )

    artifact = read_replay(store, result.replay_artifact_ref)
    diagnostic = artifact.selector_call_diagnostics[0]
    candidate_set = artifact.candidate_sets[0]
    observation = artifact.observations[0]

    assert diagnostic.document_id == artifact.document_id
    assert diagnostic.spec_version == artifact.spec_version
    assert diagnostic.field_ids == (candidate_set.field_id,)
    assert diagnostic.final_observations == (observation,)
    assert diagnostic.candidate_count_by_field == {
        candidate_set.field_id: len(candidate_set.candidates),
    }
    assert diagnostic.presented_candidate_ids_by_field == {
        candidate_set.field_id: tuple(
            candidate.candidate_id for candidate in candidate_set.candidates
        ),
    }
    assert diagnostic.presented_count_by_field == {
        candidate_set.field_id: len(candidate_set.candidates),
    }


@pytest.mark.asyncio
async def test_v2_reader_defaults_selector_call_diagnostics_empty(
    executor_with_storage,
    pydantic_spec,
    runtime,
    policy,
    doc_complete: str,
    store,
) -> None:
    result = await executor_with_storage.execute(
        document=doc_complete,
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )
    original = read_replay(store, result.replay_artifact_ref)
    payload = original.model_dump(mode="python")
    payload["schema_version"] = REPLAY_ARTIFACT_SCHEMA_V2
    payload.pop("selector_call_diagnostics", None)

    legacy_blob = msgspec.msgpack.encode(payload)
    legacy_artifact = ReplayArtifactReader().deserialize(legacy_blob)

    assert legacy_artifact.schema_version == REPLAY_ARTIFACT_SCHEMA_V2
    assert legacy_artifact.observations == original.observations
    assert legacy_artifact.selector_call_diagnostics == ()


@pytest.mark.asyncio
async def test_v1_reader_translates_legacy_selections_to_observations(
    executor_with_storage,
    pydantic_spec,
    runtime,
    policy,
    doc_complete: str,
    store,
) -> None:
    result = await executor_with_storage.execute(
        document=doc_complete,
        spec=pydantic_spec,
        runtime=runtime,
        policy=policy,
    )
    original = read_replay(store, result.replay_artifact_ref)
    payload = {
        ("selections" if key == "observations" else key): value
        for key, value in original.model_dump(mode="python").items()
    }
    payload["schema_version"] = REPLAY_ARTIFACT_SCHEMA_V1
    payload.pop("selector_call_diagnostics", None)

    legacy_blob = msgspec.msgpack.encode(payload)
    legacy_artifact = ReplayArtifactReader().deserialize(legacy_blob)

    assert legacy_artifact.schema_version == REPLAY_ARTIFACT_SCHEMA_V1
    assert legacy_artifact.observations == original.observations
    assert legacy_artifact.selector_call_diagnostics == ()

    round_trip_blob = ReplayArtifactWriter().serialize(legacy_artifact)
    assert round_trip_blob == legacy_blob
