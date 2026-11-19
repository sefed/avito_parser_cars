import re
from dataclasses import dataclass, field, replace
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag


AVITO_HOSTS = {"avito.ru", "www.avito.ru", "m.avito.ru"}
ITEM = '[data-marker="item"]'
DESCRIPTION = '[data-marker="item-description/text"], [itemprop="description"]'
EMPTY = '[data-marker="catalog-serp/empty"], [data-marker="catalog-empty"]'
DETAIL = f'[data-marker="item-view/title-info"], [data-marker="item-view/item-params"], {DESCRIPTION}'
BLOCKED = (
    "доступ ограничен", "доступ временно ограничен", "слишком много запросов",
    "подтвердите, что вы не робот", "проверяем, что вы не робот", "access denied",
    "подозрительная активность", "проверка безопасности", "доступ с вашего ip",
)
ID_SUFFIX = re.compile(r"_(\d+)/?$")
YEAR = re.compile(r"(?:^|[,·])\s*((?:19|20)\d{2})(?:\s*г\.?)?(?:\s*[,·]|\s*$)")
MILEAGE = re.compile(r"([0-9][0-9\s]*)\s*км\b", re.IGNORECASE)
RUBLES = re.compile(r"([0-9][0-9\s]*)\s*(?:₽|руб\b)", re.IGNORECASE)
CAR_FIELDS = {
    "марка": "brand",
    "модель": "model",
    "поколение": "generation",
    "тип двигателя": "fuel",
    "объём двигателя": "engine_volume",
    "объем двигателя": "engine_volume",
    "мощность": "power",
    "коробка передач": "transmission",
    "привод": "drive",
    "тип кузова": "body",
    "цвет": "color",
    "количество владельцев": "owners",
    "владельцев по птс": "owners",
    "состояние": "condition",
}


class ParseError(Exception):
    pass


@dataclass
class Listing:
    id: str
    url: str
    title: str
    price_rub: int | None = None
    year: int | None = None
    mileage_km: int | None = None
    location: str | None = None
    description: str | None = None
    characteristics: dict[str, str] = field(default_factory=dict)
    photo_urls: list[str] = field(default_factory=list)


def is_avito(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and parts.hostname in AVITO_HOSTS
            and parts.username is None
            and parts.port in (None, 443)
        )
    except ValueError:
        return False


def absolute_url(base: str, href: str) -> str:
    try:
        return urljoin(base, href)
    except ValueError:
        return ""


def value(node: Tag | None) -> str:
    return (node.get("content") or node.get_text(" ", strip=True)).strip() if node else ""


def number(raw: str) -> int | None:
    compact = "".join(raw.split())
    return int(compact) if re.fullmatch(r"[0-9]{1,15}", compact) else None


def matched_number(pattern: re.Pattern, raw: str) -> int | None:
    match = pattern.search(raw)
    return number(match[1]) if match else None


def read_price(node: Tag) -> int | None:
    price = node.select_one('[itemprop="price"][content]')
    amount = number(value(price))
    if amount is not None:
        return amount
    price = node.select_one('[data-marker="item-price"], [data-marker="item-view/item-price"]')
    return matched_number(RUBLES, value(price))


def checked_page(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    heading = " ".join(value(node).casefold() for node in soup.select("title, h1, h2"))
    if any(phrase in heading for phrase in BLOCKED) or soup.select_one(
        '[data-marker="captcha"], form[action*="captcha"]'
    ):
        raise ParseError("Авито показал страницу ограничения доступа или CAPTCHA.")
    return soup


def parse_search(html: str, url: str) -> tuple[list[Listing], str | None]:
    soup = checked_page(html)
    listings = {}
    for card in soup.select(ITEM):
        link = card.select_one('a[data-marker="item-title"][href], a[itemprop="url"][href]')
        if link is None:
            continue
        target = absolute_url(url, link["href"])
        if not is_avito(target):
            continue
        parts = urlsplit(target)
        suffix = ID_SUFFIX.search(parts.path)
        item_id = str(card.get("data-item-id", ""))
        if not item_id.isascii() or not item_id.isdigit():
            item_id = suffix[1] if suffix else ""
        title = value(link.select_one('[itemprop="name"]')) or value(link) or link.get("title", "").strip()
        if not item_id or not title or "/avtomobili/" not in parts.path.casefold():
            continue
        snippets = " ".join(value(node) for node in card.select(
            '[data-marker="item-specific-params"], [data-marker="item-params"]'
        ))
        location = value(card.select_one('[data-marker="item-address"], [data-marker="item-location"]'))
        listings.setdefault(item_id, Listing(
            id=item_id,
            url=urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")),
            title=title,
            price_rub=read_price(card),
            year=matched_number(YEAR, title) or matched_number(YEAR, snippets),
            mileage_km=matched_number(MILEAGE, f"{title} · {snippets}"),
            location=location or None,
        ))
    if not listings and not soup.select_one(EMPTY):
        raise ParseError("Карточки автомобилей не найдены. Проверьте ссылку, категорию и разметку выдачи.")

    next_link = soup.select_one('a[data-marker="pagination-button/next"][href], a[rel~="next"][href]')
    next_url = absolute_url(url, next_link["href"]) if next_link else None
    return list(listings.values()), next_url if next_url and is_avito(next_url) else None


def parse_detail(html: str, listing: Listing) -> Listing:
    soup = checked_page(html)
    if not soup.select_one(DETAIL) and not soup.select_one('[itemprop="price"]'):
        raise ParseError("Разметка карточки объявления не распознана.")

    characteristics = {}
    year = listing.year
    mileage = listing.mileage_km
    for row in soup.select('[data-marker="item-view/item-params"] li, li[data-marker="item-view/item-params-item"]'):
        label, separator, raw = value(row).partition(":")
        label = label.strip().casefold()
        raw = raw.strip()
        if not separator or not raw:
            continue
        if label in CAR_FIELDS:
            characteristics[CAR_FIELDS[label]] = raw
        elif label in {"год выпуска", "год"}:
            year = matched_number(YEAR, raw) or year
        elif label == "пробег":
            parsed = number(raw)
            if parsed is None:
                parsed = matched_number(MILEAGE, raw)
            if parsed is not None:
                mileage = parsed

    photos = []
    for image in soup.select(
        'meta[property="og:image"], [data-marker^="image-frame"] img, '
        '[data-marker^="item-view/gallery"] img, [data-marker^="gallery-frame"] img'
    ):
        href = image.get("data-src") or image.get("src") or image.get("content")
        if href:
            target = absolute_url(listing.url, href)
            if target.startswith(("https://", "http://")) and target not in photos:
                photos.append(target)
    price = read_price(soup)
    return replace(
        listing,
        price_rub=price if price is not None else listing.price_rub,
        year=year,
        mileage_km=mileage,
        location=value(soup.select_one('[data-marker="item-view/item-address"]')) or listing.location,
        description=value(soup.select_one(DESCRIPTION)) or None,
        characteristics=characteristics,
        photo_urls=photos,
    )
