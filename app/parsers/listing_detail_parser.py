from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

PARSER_VERSION = "listing-detail-v1"
VOLATILE_QUERY_PREFIXES = ("utm_",)
VOLATILE_QUERY_KEYS = {"context", "src", "from", "localPriority", "cd", "iid", "x", "p"}

_CONTACT_REDACTION = "[redacted_contact]"
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)")
_CONTACT_PAIR_PATTERN = re.compile(
    r"\b(?:telegram|телеграм|whatsapp|ватсап|wa|contact|контакт|phone|телефон|email|почта)\b\s*[:=@-]?\s*(?:\+?\d[\d\s().-]{5,}\d|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|@[A-Z0-9_]{3,})",
    re.I,
)

LIMITS = {
    "title": 300,
    "description_text": 10_000,
    "address_text": 500,
    "metro_text": 300,
    "price_text": 200,
    "area_text": 100,
    "published_label": 200,
    "seller_name": 300,
    "seller_type": 100,
    "category": 300,
    "raw_text_excerpt": 2_000,
    "error_type": 100,
    "error_message": 1_000,
}


@dataclass
class ParsedListingDetail:
    source_url: str | None = None
    canonical_url: str | None = None
    source_host: str | None = None
    parse_status: str = "failed"
    parser_version: str = PARSER_VERSION
    content_hash: str | None = None
    title: str = ""
    description_text: str = ""
    address_text: str = ""
    metro_text: str = ""
    price_text: str = ""
    area_text: str = ""
    published_label: str = ""
    published_at: datetime | None = None
    seller_name: str = ""
    seller_type: str = "unknown"
    category: str = ""
    attributes_json: dict[str, str] = field(default_factory=dict)
    facts_json: dict[str, object] = field(default_factory=dict)
    photos_count: int | None = None
    raw_text_excerpt: str = ""
    extracted_fields_count: int = 0
    truncated_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None


def normalize_whitespace(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def canonicalize_url(url: str | None) -> tuple[str | None, str | None]:
    if not url:
        return None, None
    parts = urlsplit(url.strip())
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key in VOLATILE_QUERY_KEYS or any(key.startswith(prefix) for prefix in VOLATILE_QUERY_PREFIXES):
            continue
        query.append((key, value))
    canonical = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(sorted(query)), ""))
    return canonical, parts.netloc.lower() or None


def redact_contact_like_text(value: str | None) -> str:
    """Redact obvious contact-like values before text is persisted."""
    text = normalize_whitespace(value)
    if not text:
        return ""
    text = _CONTACT_PAIR_PATTERN.sub(_CONTACT_REDACTION, text)
    text = _EMAIL_PATTERN.sub(_CONTACT_REDACTION, text)
    text = _PHONE_PATTERN.sub(_CONTACT_REDACTION, text)
    return normalize_whitespace(text)


def _bounded(value: str, field_name: str, truncated: list[str]) -> str:
    value = normalize_whitespace(value)
    limit = LIMITS[field_name]
    if len(value) > limit:
        truncated.append(field_name)
        return value[:limit]
    return value


def _first_text(soup: BeautifulSoup, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            text = normalize_whitespace(node.get_text(" "))
            if text:
                return text
    return ""


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        node = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if node and node.get("content"):
            return normalize_whitespace(str(node["content"]))
    return ""


def _extract_attributes(soup: BeautifulSoup, truncated: list[str]) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for row in soup.select('[data-marker="item-params"] li, .params-paramsList__item, .item-params-list-item')[:80]:
        text = normalize_whitespace(row.get_text(" "))
        if ":" in text:
            key, value = [normalize_whitespace(part) for part in text.split(":", 1)]
            if key and value and not _looks_contact_key(key):
                attrs[_bounded(key, "category", truncated)] = _bounded(value, "address_text", truncated)
    for node in soup.select("[data-detail-attr]")[:80]:
        key = normalize_whitespace(node.get("data-detail-attr"))
        value = normalize_whitespace(node.get_text(" "))
        if key and value and not _looks_contact_key(key):
            attrs[_bounded(key, "category", truncated)] = _bounded(value, "address_text", truncated)
    return dict(sorted(attrs.items()))


def _looks_contact_key(value: str) -> bool:
    return bool(re.search(r"phone|телефон|contact|контакт|whatsapp|telegram|email|почта", value, re.I))


def compute_content_hash(parsed: ParsedListingDetail) -> str:
    payload = {
        "title": parsed.title,
        "description_text": parsed.description_text,
        "address_text": parsed.address_text,
        "metro_text": parsed.metro_text,
        "price_text": parsed.price_text,
        "area_text": parsed.area_text,
        "published_label": parsed.published_label,
        "published_at": parsed.published_at.isoformat() if parsed.published_at else None,
        "seller_type": parsed.seller_type,
        "category": parsed.category,
        "attributes_json": parsed.attributes_json,
        "facts_json": parsed.facts_json,
        "photos_count": parsed.photos_count,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def parse_listing_detail_html(html: str, source_url: str | None = None) -> ParsedListingDetail:
    truncated: list[str] = []
    warnings: list[str] = []
    canonical_url, source_host = canonicalize_url(source_url)
    try:
        soup = BeautifulSoup(html or "", "lxml")
        for node in soup.select("script, style, noscript, [data-marker*='phone'], [class*='phone'], [class*='contact'], [data-marker*='contact']"):
            node.decompose()
        text = normalize_whitespace(soup.get_text(" "))
        title = _first_text(soup, ('[data-marker="item-view/title-info"]', 'h1[data-marker="item-view/title-info"]', "h1")) or _meta(soup, "og:title")
        description = _first_text(soup, ('[data-marker="item-view/item-description"]', '[itemprop="description"]', ".item-description")) or _meta(soup, "og:description")
        price = _first_text(soup, ('[data-marker="item-view/item-price"]', '[itemprop="price"]', ".js-item-price"))
        address = _first_text(soup, ('[data-marker="item-view/item-address"]', '[itemprop="address"]', ".style-item-address"))
        metro = _first_text(soup, ('[data-marker="item-view/metro"]', ".item-address-georeferences-item__content"))
        published = _first_text(soup, ('[data-marker="item-view/item-date"]', ".title-info-metadata-item"))
        seller = _first_text(soup, ('[data-marker="seller-info/name"]', '[data-marker="seller-info/label"]'))
        category = _first_text(soup, ('[data-marker="breadcrumbs"]', "nav[aria-label='breadcrumb']"))
        area = _first_text(soup, ('[data-marker="item-view/item-area"]',))
        attrs = _extract_attributes(soup, truncated)
        if not area:
            for key, value in attrs.items():
                if re.search(r"площадь|area", key, re.I):
                    area = value
                    break
        photos = len({img.get("src") for img in soup.select('[data-marker*="photo"] img, img[itemprop="image"]') if img.get("src")}) or None
        seller_type = "unknown"
        seller_blob = f"{seller} {text[:2000]}"
        if re.search(r"агентств|риелтор|компан|ооо|ип|зао|ао", seller_blob, re.I):
            seller_type = "agency"
        elif re.search(r"собственник|частн", seller_blob, re.I):
            seller_type = "owner"
        facts = {"has_description": bool(description), "has_public_seller_name": bool(seller)}
        parsed = ParsedListingDetail(
            source_url=source_url,
            canonical_url=canonical_url,
            source_host=source_host,
            title=_bounded(title, "title", truncated),
            description_text=_bounded(redact_contact_like_text(description), "description_text", truncated),
            address_text=_bounded(address, "address_text", truncated),
            metro_text=_bounded(metro, "metro_text", truncated),
            price_text=_bounded(price, "price_text", truncated),
            area_text=_bounded(area, "area_text", truncated),
            published_label=_bounded(published, "published_label", truncated),
            seller_name=_bounded(redact_contact_like_text(seller), "seller_name", truncated),
            seller_type=_bounded(seller_type, "seller_type", truncated),
            category=_bounded(category, "category", truncated),
            attributes_json=attrs,
            facts_json=facts,
            photos_count=photos,
            raw_text_excerpt=_bounded(redact_contact_like_text(text), "raw_text_excerpt", truncated),
            truncated_fields=sorted(set(truncated))[:50],
            warnings=warnings[:50],
        )
        parsed.extracted_fields_count = sum(bool(getattr(parsed, f)) for f in ("title", "description_text", "address_text", "metro_text", "price_text", "area_text", "published_label", "seller_name", "category")) + len(attrs)
        if parsed.extracted_fields_count == 0:
            parsed.parse_status = "failed"
            parsed.error_type = "no_fields_extracted"
            parsed.error_message = "No supported public listing detail fields were extracted."
        elif parsed.extracted_fields_count < 3:
            parsed.parse_status = "partial"
            parsed.warnings.append("Only a small number of public detail fields were extracted.")
        else:
            parsed.parse_status = "success"
        parsed.content_hash = compute_content_hash(parsed)
        return parsed
    except Exception as exc:  # parser boundary: return diagnostics, do not crash service
        return ParsedListingDetail(source_url=source_url, canonical_url=canonical_url, source_host=source_host, error_type=exc.__class__.__name__[:100], error_message=str(exc)[:1000])
