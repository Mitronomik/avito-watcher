from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.knowledge_note import ALLOWED_KNOWLEDGE_NOTE_TYPES

KnowledgeNoteType = Literal["rulebook", "false_positive", "domain_note"]

MAX_TITLE_LENGTH = 200
MAX_BODY_LENGTH = 10_000
MAX_TAGS = 50
MAX_TAG_LENGTH = 64
MAX_SOURCE_LENGTH = 100
MAX_SOURCE_REF_LENGTH = 500
MAX_SEARCH_LIMIT = 20
MAX_SNIPPET_LENGTH = 500


def normalize_profile(value: str | None) -> str:
    if value is None:
        return "global"
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    normalized = normalized.strip("_")
    if not normalized:
        raise ValueError("profile must be non-empty")
    if len(normalized) > 128:
        raise ValueError("profile must be <= 128 characters")
    return normalized


def normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("tags_json must be a list of strings")
    tags: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError("tags_json must be a list of strings")
        tag = item.strip().lower()
        if not tag:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            raise ValueError("each tag must be <= 64 characters")
        tags.add(tag)
    if len(tags) > MAX_TAGS:
        raise ValueError("tags_json must contain <= 50 tags")
    return sorted(tags)


class KnowledgeNoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_type: KnowledgeNoteType
    profile: str = "global"
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    body_md: str = Field(min_length=1, max_length=MAX_BODY_LENGTH)
    tags_json: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    source: str | None = Field(default=None, max_length=MAX_SOURCE_LENGTH)
    source_ref: str | None = Field(default=None, max_length=MAX_SOURCE_REF_LENGTH)
    priority: int = 0
    is_active: bool = True

    @field_validator("note_type", mode="before")
    @classmethod
    def _validate_note_type(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip().lower()
        if value not in ALLOWED_KNOWLEDGE_NOTE_TYPES:
            raise ValueError("note_type must be one of: rulebook, false_positive, domain_note")
        return value

    @field_validator("profile", mode="before")
    @classmethod
    def _normalize_profile(cls, value: Any) -> str:
        if value is None or isinstance(value, str):
            return normalize_profile(value)
        raise ValueError("profile must be a string")

    @field_validator("title", "body_md", mode="before")
    @classmethod
    def _trim_required_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        if not value:
            raise ValueError("title must be non-empty")
        if len(value) > MAX_TITLE_LENGTH:
            raise ValueError("title must be <= 200 characters")
        return value

    @field_validator("body_md")
    @classmethod
    def _validate_body(cls, value: str) -> str:
        if not value:
            raise ValueError("body_md must be non-empty")
        if len(value) > MAX_BODY_LENGTH:
            raise ValueError("body_md must be <= 10000 characters")
        return value

    @field_validator("tags_json", mode="before")
    @classmethod
    def _normalize_tags(cls, value: Any) -> list[str]:
        return normalize_tags(value)

    @field_validator("metadata_json", mode="before")
    @classmethod
    def _normalize_metadata(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("metadata_json must be an object")
        return value

    @field_validator("source", "source_ref", mode="before")
    @classmethod
    def _trim_optional_string(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        raise ValueError("value must be a string")


class KnowledgeNoteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_type: KnowledgeNoteType | None = None
    profile: str | None = None
    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH)
    body_md: str | None = Field(default=None, max_length=MAX_BODY_LENGTH)
    tags_json: list[str] | None = Field(default=None, max_length=MAX_TAGS)
    metadata_json: dict[str, Any] | None = None
    source: str | None = Field(default=None, max_length=MAX_SOURCE_LENGTH)
    source_ref: str | None = Field(default=None, max_length=MAX_SOURCE_REF_LENGTH)
    priority: int | None = None
    is_active: bool | None = None

    _validate_note_type = field_validator("note_type", mode="before")(KnowledgeNoteCreate._validate_note_type.__func__)
    _normalize_profile = field_validator("profile", mode="before")(KnowledgeNoteCreate._normalize_profile.__func__)
    _trim_required_string = field_validator("title", "body_md", mode="before")(
        KnowledgeNoteCreate._trim_required_string.__func__
    )
    _validate_title = field_validator("title")(KnowledgeNoteCreate._validate_title.__func__)
    _validate_body = field_validator("body_md")(KnowledgeNoteCreate._validate_body.__func__)
    _normalize_tags = field_validator("tags_json", mode="before")(KnowledgeNoteCreate._normalize_tags.__func__)
    _normalize_metadata = field_validator("metadata_json", mode="before")(KnowledgeNoteCreate._normalize_metadata.__func__)
    _trim_optional_string = field_validator("source", "source_ref", mode="before")(
        KnowledgeNoteCreate._trim_optional_string.__func__
    )


class KnowledgeNoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    note_type: str
    profile: str
    title: str
    body_md: str
    tags_json: list[str]
    metadata_json: dict[str, Any]
    source: str | None
    source_ref: str | None
    priority: int
    is_active: bool


class KnowledgeNoteSearchResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    note_type: str
    profile: str
    title: str
    snippet: str
    tags_json: list[str]
    priority: int
    lexical_score: int
