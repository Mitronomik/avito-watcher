from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.knowledge_note import KnowledgeNote
from app.schemas.knowledge_notes import KnowledgeNoteCreate, KnowledgeNoteUpdate, normalize_profile, normalize_tags


class KnowledgeNoteRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_note(self, data: KnowledgeNoteCreate | dict) -> KnowledgeNote:
        payload = data if isinstance(data, KnowledgeNoteCreate) else KnowledgeNoteCreate.model_validate(data)
        note = KnowledgeNote(**payload.model_dump())
        self.db.add(note)
        self.db.flush()
        return note

    def get_note(self, note_id: int) -> KnowledgeNote | None:
        return self.db.get(KnowledgeNote, note_id)

    def list_notes(
        self,
        *,
        profile: str | None = None,
        note_types: list[str] | None = None,
        tags: list[str] | None = None,
        active_only: bool = True,
        limit: int = 50,
    ) -> list[KnowledgeNote]:
        if limit <= 0:
            return []
        limit = min(limit, 100)
        stmt = select(KnowledgeNote)
        if active_only:
            stmt = stmt.where(KnowledgeNote.is_active.is_(True))
        if profile is not None:
            stmt = stmt.where(KnowledgeNote.profile == normalize_profile(profile))
        if note_types:
            allowed = [KnowledgeNoteCreate.model_validate({"note_type": item, "title": "x", "body_md": "x"}).note_type for item in note_types]
            stmt = stmt.where(KnowledgeNote.note_type.in_(allowed))
        if tags:
            wanted = set(normalize_tags(tags))
            notes = list(self.db.scalars(stmt).all())
            filtered = [note for note in notes if wanted.intersection(set(note.tags_json or []))]
            return sorted(filtered, key=lambda n: (n.priority, n.updated_at, n.id), reverse=True)[:limit]
        stmt = stmt.order_by(KnowledgeNote.priority.desc(), KnowledgeNote.updated_at.desc(), KnowledgeNote.id.desc()).limit(limit)
        return list(self.db.scalars(stmt).all())

    def update_note(self, note: KnowledgeNote, data: KnowledgeNoteUpdate | dict) -> KnowledgeNote:
        payload = data if isinstance(data, KnowledgeNoteUpdate) else KnowledgeNoteUpdate.model_validate(data)
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(note, key, value)
        self.db.flush()
        return note

    def deactivate_note(self, note: KnowledgeNote) -> KnowledgeNote:
        note.is_active = False
        self.db.flush()
        return note
