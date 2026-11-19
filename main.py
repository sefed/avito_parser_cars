import argparse
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from avito import DETAIL, EMPTY, ITEM, ParseError, is_avito, parse_detail, parse_search
from storage import save


DEFAULT_URL = "https://www.avito.ru/moskva/avtomobili"
TIMEOUT_MS = 15_000
ABSENT = {404, 410}
ACCESS_LIMIT = {401, 403, 429}
PAGE_SIGNALS = {
    "search": f"{ITEM}, {EMPTY}",
    "detail": DETAIL,
}


@contextmanager
def browser_loader(channel, debug=False):
    try:
        from playwright.sync_api import Error, TimeoutError, sync_playwright
    except ImportError as error:
        raise ParseError("Установите зависимости командой python -m pip install -r requirements.txt.") from error

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=channel)
            try:
                page = browser.new_page(locale="ru-RU")
                last_navigation = None

                def load(url, listing=None):
                    nonlocal last_navigation
                    kind = "search" if listing is None else "detail"
                    if last_navigation is not None:
                        time.sleep(max(0, 2 - (time.monotonic() - last_navigation)))
                    last_navigation = time.monotonic()
                    response = page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                    if debug and response is not None:
                        stage = "выдача" if listing is None else f"карточка {listing.id}"
                        final_url = urlsplit(page.url)._replace(query="", fragment="").geturl()
                        retry_after = response.headers.get("retry-after", "отсутствует")
                        try:
                            title = page.title()
                        except Error:
                            title = "недоступен"
                        print(
                            f"[debug] {stage} | HTTP {response.status} | URL={final_url} | "
                            f"title={title!r} | Retry-After={retry_after}",
                            file=sys.stderr,
                        )
                    if response is None or not is_avito(page.url):
                        raise ParseError("Авито перенаправил запрос за пределы сайта или вернул пустой ответ.")
                    status = response.status
                    if status in ACCESS_LIMIT:
                        raise ParseError(f"Авито ограничил доступ (HTTP {status}).")
                    if status in ABSENT and kind == "detail":
                        return None, page.url
                    if status >= 400:
                        raise ParseError(f"Авито вернул HTTP {status}.")
                    if "captcha" in urlsplit(page.url).path.casefold():
                        raise ParseError("Авито показал CAPTCHA.")
                    try:
                        signals = f'{PAGE_SIGNALS[kind]}, [data-marker="captcha"], form[action*="captcha"]'
                        page.locator(signals).first.wait_for(state="attached", timeout=5_000)
                    except TimeoutError:
                        pass
                    if not is_avito(page.url):
                        raise ParseError("Страница перенаправлена за пределы Авито.")
                    return page.content(), page.url

                yield load
            finally:
                browser.close()
    except Error as error:
        if channel is None and "Executable doesn't exist" in str(error):
            raise ParseError("Установите Chromium командой python -m playwright install chromium.") from error
        reason = str(error).splitlines()[0]
        raise ParseError(f"Ошибка браузера. {reason}") from error


def collect(load, url, args, results):
    visited = set()
    for _ in range(args.pages):
        if not url or url in visited:
            break
        visited.add(url)
        html, actual_url = load(url)
        visited.add(actual_url)
        listings, url = parse_search(html, actual_url)
        for listing in listings:
            if listing.id in results:
                continue
            results[listing.id] = listing
            if args.details:
                detail, _ = load(listing.url, listing)
                if detail is None:
                    print(f"Карточка {listing.id} недоступна. Сохранены данные из выдачи.", file=sys.stderr)
                else:
                    results[listing.id] = parse_detail(detail, listing)
            if len(results) >= args.limit:
                return


def positive(value):
    count = int(value)
    if count < 1:
        raise argparse.ArgumentTypeError("укажите целое число больше нуля")
    return count


def main():
    parser = argparse.ArgumentParser(description="Выгрузка автомобильных объявлений Авито в CSV и SQLite.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--url", help="HTTPS-ссылка на выдачу автомобилей с фильтрами.")
    source.add_argument("--html", type=Path, help="Локальный HTML выдачи. Сеть не используется.")
    parser.add_argument("--pages", type=positive, default=1, help="Максимум страниц выдачи, по умолчанию 1.")
    parser.add_argument("--limit", type=positive, default=10, help="Максимум объявлений за запуск, по умолчанию 10.")
    parser.add_argument("--details", action="store_true", help="Прочитать описания, характеристики и ссылки фотографий.")
    parser.add_argument(
        "--browser", choices=("chromium", "msedge"), default="chromium",
        help="Браузер для онлайн-сбора. Можно использовать установленный Microsoft Edge.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("output"), help="Каталог CSV и SQLite.")
    parser.add_argument("--debug", action="store_true", help="Показать диагностику загрузки страниц Авито.")
    args = parser.parse_args()
    url = args.url if args.url is not None else DEFAULT_URL
    if not is_avito(url):
        parser.error("--url должен быть HTTPS-ссылкой на avito.ru")
    if args.html and args.pages != 1:
        parser.error("--html поддерживает одну страницу выдачи")

    results = {}
    exit_code = 0
    try:
        if args.html:
            def load_local(source_url, listing=None):
                if listing is None:
                    return args.html.read_text(encoding="utf-8-sig"), source_url
                detail = args.html.with_name(f"{listing.id}.html")
                try:
                    return detail.read_text(encoding="utf-8-sig"), source_url
                except FileNotFoundError:
                    return None, source_url

            collect(load_local, url, args, results)
        else:
            channel = "msedge" if args.browser == "msedge" else None
            with browser_loader(channel, debug=args.debug) as load:
                collect(load, url, args, results)
    except (ParseError, OSError, UnicodeError) as error:
        print(f"Сбор остановлен. {error}", file=sys.stderr)
        exit_code = 1
    except KeyboardInterrupt:
        print("Сбор прерван пользователем.", file=sys.stderr)
        exit_code = 130

    if results or not exit_code:
        try:
            total = save(results.values(), args.output_dir)
        except (OSError, ValueError) as error:
            print(f"Ошибка записи результатов. {error}", file=sys.stderr)
            return 1
        print(f"Собрано за запуск {len(results)}. Всего в выгрузке {total}.")
        print(f"Результаты сохранены в {args.output_dir.resolve()}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
