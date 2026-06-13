import pytest
from pydantic import ValidationError
from sqlalchemy import inspect

from app.db.base import Base
from app.models.knowledge_note import KnowledgeNote
from app.repositories.knowledge_notes import KnowledgeNoteRepository
from app.schemas.knowledge_notes import KnowledgeNoteCreate
from app.services.knowledge_retrieval import KnowledgeRetrievalService


def test_knowledge_note_model_is_registered_and_table_has_expected_shape(db_session):
    assert "knowledge_notes" in Base.metadata.tables
    inspector = inspect(db_session.bind)
    columns = {column["name"] for column in inspector.get_columns("knowledge_notes")}
    assert {
        "id",
        "note_type",
        "profile",
        "title",
        "body_md",
        "tags_json",
        "metadata_json",
        "source",
        "source_ref",
        "priority",
        "is_active",
        "created_at",
        "updated_at",
    }.issubset(columns)
    indexes = {index["name"] for index in inspector.get_indexes("knowledge_notes")}
    assert "ix_knowledge_notes_note_type" in indexes
    assert "ix_knowledge_notes_profile" in indexes
    assert "ix_knowledge_notes_is_active" in indexes
    assert "ix_knowledge_notes_priority" in indexes


def test_create_get_and_defaults(db_session):
    service = KnowledgeRetrievalService(db_session)

    created = service.create_note(
        note_type="rulebook",
        title=" Fresh commercial rent ",
        body_md=" Prefer fresh listings with valid published_at. ",
        tags_json=[" Fresh ", "fresh", " Published_At "],
        metadata_json=None,
    )

    assert created.id
    assert created.profile == "global"
    assert created.title == "Fresh commercial rent"
    assert created.body_md == "Prefer fresh listings with valid published_at."
    assert created.tags_json == ["fresh", "published_at"]
    assert created.metadata_json == {}
    assert created.priority == 0
    assert created.is_active is True

    fetched = service.get_note(created.id)
    assert fetched == created
    model = db_session.get(KnowledgeNote, created.id)
    assert model.created_at is not None
    assert model.updated_at is not None


def test_repository_create_list_update_and_deactivate(db_session):
    repo = KnowledgeNoteRepository(db_session)
    first = repo.create_note(
        {
            "note_type": "false_positive",
            "profile": "Commercial Rent",
            "title": "Parking false positives",
            "body_md": "гараж and parking can be false positives",
            "priority": 5,
        }
    )
    second = repo.create_note(
        KnowledgeNoteCreate(note_type="domain_note", title="Questions", body_md="Ask about signage", priority=1)
    )

    assert repo.get_note(first.id) == first
    assert [note.id for note in repo.list_notes(limit=10)] == [first.id, second.id]
    assert [note.id for note in repo.list_notes(profile="commercial_rent", limit=10)] == [first.id]

    repo.update_note(first, {"title": "Updated parking", "tags_json": ["Parking", ""]})
    assert first.title == "Updated parking"
    assert first.tags_json == ["parking"]

    repo.deactivate_note(first)
    assert first.is_active is False
    assert [note.id for note in repo.list_notes(limit=10)] == [second.id]
    assert [note.id for note in repo.list_notes(active_only=False, limit=10)] == [first.id, second.id]


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"note_type": "unknown", "title": "x", "body_md": "x"}, "note_type"),
        ({"note_type": "rulebook", "title": "   ", "body_md": "x"}, "title"),
        ({"note_type": "rulebook", "title": "x", "body_md": "   "}, "body_md"),
        ({"note_type": "rulebook", "title": "x" * 201, "body_md": "x"}, "title"),
        ({"note_type": "rulebook", "title": "x", "body_md": "x" * 10001}, "body_md"),
        ({"note_type": "rulebook", "title": "x", "body_md": "x", "tags_json": "bad"}, "tags_json"),
        ({"note_type": "rulebook", "title": "x", "body_md": "x", "tags_json": [1]}, "tags_json"),
        ({"note_type": "rulebook", "title": "x", "body_md": "x", "metadata_json": []}, "metadata_json"),
        ({"note_type": "rulebook", "profile": "   ", "title": "x", "body_md": "x"}, "profile"),
        ({"note_type": "rulebook", "title": "x", "body_md": "x", "source": "x" * 101}, "source"),
        ({"note_type": "rulebook", "title": "x", "body_md": "x", "source_ref": "x" * 501}, "source_ref"),
    ],
)
def test_validation_rejects_invalid_notes(payload, message):
    with pytest.raises(ValidationError) as exc_info:
        KnowledgeNoteCreate.model_validate(payload)

    assert message in str(exc_info.value)


def test_validation_normalizes_profile_tags_source_and_active_state():
    note = KnowledgeNoteCreate.model_validate(
        {
            "note_type": " DOMAIN_NOTE ",
            "profile": " Commercial Rent ",
            "title": " Domain note ",
            "body_md": " Body ",
            "tags_json": [" Parking ", "parking", "", "Гараж"],
            "source": " manual ",
            "source_ref": "  ",
            "priority": 3,
            "is_active": False,
        }
    )

    assert note.note_type == "domain_note"
    assert note.profile == "commercial_rent"
    assert note.tags_json == ["parking", "гараж"]
    assert note.source == "manual"
    assert note.source_ref is None
    assert note.priority == 3
    assert note.is_active is False
