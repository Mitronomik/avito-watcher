from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.knowledge_note import KnowledgeNote
from app.repositories.knowledge_notes import KnowledgeNoteRepository
from app.schemas.knowledge_notes import (
    MAX_SEARCH_LIMIT,
    MAX_SNIPPET_LENGTH,
    KnowledgeNoteCreate,
    KnowledgeNoteRead,
    KnowledgeNoteSearchResult,
    KnowledgeNoteUpdate,
    normalize_profile,
    normalize_tags,
)

_QUERY_SPLIT_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class KnowledgeSearchRequest:
    query: str
    profile: str | None = None
    note_types: list[str] | None = None
    tags: list[str] | None = None
    limit: int = 5


class KnowledgeRetrievalService:
    """Local, deterministic lexical retrieval for RAG v0 knowledge notes."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = KnowledgeNoteRepository(db)

    def create_note(self, **kwargs) -> KnowledgeNoteRead:
        note = self.repo.create_note(KnowledgeNoteCreate.model_validate(kwargs))
        return KnowledgeNoteRead.model_validate(note)

    def get_note(self, note_id: int) -> KnowledgeNoteRead | None:
        note = self.repo.get_note(note_id)
        return KnowledgeNoteRead.model_validate(note) if note is not None else None

    def list_notes(
        self,
        *,
        profile: str | None = None,
        note_types: list[str] | None = None,
        tags: list[str] | None = None,
        active_only: bool = True,
        limit: int = 50,
    ) -> list[KnowledgeNoteRead]:
        notes = self.repo.list_notes(
            profile=profile,
            note_types=note_types,
            tags=tags,
            active_only=active_only,
            limit=limit,
        )
        return [KnowledgeNoteRead.model_validate(note) for note in notes]

    def update_note(self, note_id: int, **kwargs) -> KnowledgeNoteRead:
        note = self.repo.get_note(note_id)
        if note is None:
            raise ValueError("knowledge note not found")
        updated = self.repo.update_note(note, KnowledgeNoteUpdate.model_validate(kwargs))
        return KnowledgeNoteRead.model_validate(updated)

    def deactivate_note(self, note_id: int) -> KnowledgeNoteRead:
        note = self.repo.get_note(note_id)
        if note is None:
            raise ValueError("knowledge note not found")
        return KnowledgeNoteRead.model_validate(self.repo.deactivate_note(note))

    def search_notes(
        self,
        query: str,
        profile: str | None = None,
        note_types: list[str] | None = None,
        tags: list[str] | None = None,
        limit: int = 5,
    ) -> list[KnowledgeNoteSearchResult]:
        tokens = _query_tokens(query)
        if not tokens:
            raise ValueError("search query must be non-empty")
        if limit <= 0:
            raise ValueError("limit must be positive")
        limit = min(limit, MAX_SEARCH_LIMIT)
        normalized_profile = normalize_profile(profile) if profile is not None else None
        normalized_types = _normalize_note_types(note_types)
        normalized_tags = set(normalize_tags(tags)) if tags is not None else None

        stmt = select(KnowledgeNote).where(KnowledgeNote.is_active.is_(True))
        if normalized_profile is not None:
            stmt = stmt.where(KnowledgeNote.profile.in_(["global", normalized_profile]))
        if normalized_types:
            stmt = stmt.where(KnowledgeNote.note_type.in_(normalized_types))
        candidates = list(self.db.scalars(stmt).all())
        scored: list[tuple[int, KnowledgeNote]] = []
        for note in candidates:
            note_tags = set(note.tags_json or [])
            if normalized_tags is not None and not normalized_tags.intersection(note_tags):
                continue
            searchable = _searchable_text(note)
            lexical_score = sum(1 for token in tokens if token in searchable)
            if lexical_score > 0:
                scored.append((lexical_score, note))

        scored.sort(key=lambda item: (item[1].priority, item[0], item[1].updated_at, item[1].id), reverse=True)
        return [_to_search_result(note, lexical_score, tokens) for lexical_score, note in scored[:limit]]


def _query_tokens(query: str) -> list[str]:
    if not isinstance(query, str):
        raise ValueError("search query must be a string")
    return sorted({token for token in _QUERY_SPLIT_RE.split(query.strip().lower()) if token})


def _normalize_note_types(note_types: list[str] | None) -> list[str] | None:
    if note_types is None:
        return None
    if not isinstance(note_types, list):
        raise ValueError("note_types must be a list")
    normalized: list[str] = []
    for item in note_types:
        payload = KnowledgeNoteCreate.model_validate({"note_type": item, "title": "x", "body_md": "x"})
        normalized.append(payload.note_type)
    return sorted(set(normalized))


def _searchable_text(note: KnowledgeNote) -> str:
    tags = " ".join(note.tags_json or [])
    return f"{note.title} {note.body_md} {tags}".lower()


def _to_search_result(note: KnowledgeNote, lexical_score: int, tokens: list[str]) -> KnowledgeNoteSearchResult:
    return KnowledgeNoteSearchResult(
        id=note.id,
        note_type=note.note_type,
        profile=note.profile,
        title=note.title,
        snippet=_snippet(note.body_md, tokens),
        tags_json=list(note.tags_json or []),
        priority=note.priority,
        lexical_score=lexical_score,
        source=note.source,
        source_ref=note.source_ref,
    )


def _snippet(body: str, tokens: list[str]) -> str:
    body = body.strip()
    lower_body = body.lower()
    start = 0
    positions = [lower_body.find(token) for token in tokens if token in lower_body]
    if positions:
        start = max(min(positions) - 80, 0)
    snippet = body[start : start + MAX_SNIPPET_LENGTH]
    return snippet
