def format_price(price: float | None) -> str:
    if price is None:
        return "—"
    return f"{int(price):,}".replace(",", " ") + " ₽"


def build_listing_message(card: dict, llm_summary: str) -> str:
    return (
        f"🏠 {card.get('title') or 'Новое объявление'}\n"
        f"💰 {format_price(card.get('price'))}\n"
        f"📍 {card.get('address') or 'Адрес не указан'}\n"
        f"📐 {card.get('area_m2') or '—'} м² | Комнат: {card.get('rooms') or '—'}\n"
        f"🔗 {card.get('url') or ''}\n\n"
        f"🤖 {llm_summary or 'Без summary'}"
    )
