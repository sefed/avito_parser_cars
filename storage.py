import csv
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile


COLUMNS = (
    "id", "url", "title", "price_rub", "year", "mileage_km", "location", "description",
    "characteristics", "photo_urls",
)


def csv_value(value):
    # Spreadsheet apps can interpret these prefixes in externally supplied text as formulas.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def save(listings, directory: Path) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    columns = ", ".join(COLUMNS)
    placeholders = ", ".join("?" for _ in COLUMNS)
    updates = ", ".join(
        f"{column} = COALESCE(excluded.{column}, listings.{column})" for column in COLUMNS[1:]
    )
    rows = (
        (
            ad.id, ad.url, ad.title, ad.price_rub, ad.year, ad.mileage_km, ad.location, ad.description,
            json.dumps(ad.characteristics, ensure_ascii=False) if ad.characteristics else None,
            json.dumps(ad.photo_urls, ensure_ascii=False) if ad.photo_urls else None,
        )
        for ad in listings
    )
    pending_csv = None
    try:
        with closing(sqlite3.connect(directory / "cars.sqlite3")) as database:
            with database:
                database.execute("""
                    CREATE TABLE IF NOT EXISTS listings (
                        id TEXT PRIMARY KEY NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL,
                        price_rub INTEGER, year INTEGER, mileage_km INTEGER, location TEXT,
                        description TEXT, characteristics TEXT, photo_urls TEXT
                    )
                """)
                database.executemany(
                    f"INSERT INTO listings ({columns}) VALUES ({placeholders}) "
                    f"ON CONFLICT(id) DO UPDATE SET {updates}",
                    rows,
                )

            # Serialize CSV publication with other writers to the same database.
            with database:
                database.execute("BEGIN IMMEDIATE")
                snapshot = database.execute(f"SELECT {columns} FROM listings ORDER BY id")
                total = 0
                with NamedTemporaryFile(
                    mode="w", encoding="utf-8-sig", newline="", dir=directory, suffix=".tmp", delete=False,
                ) as stream:
                    pending_csv = Path(stream.name)
                    writer = csv.writer(stream, delimiter=";")
                    writer.writerow(COLUMNS)
                    for row in snapshot:
                        writer.writerow(csv_value(value) for value in row)
                        total += 1
                pending_csv.replace(directory / "cars.csv")
        return total
    except sqlite3.Error as error:
        raise ValueError(f"SQLite: {error}") from error
    finally:
        if pending_csv is not None:
            pending_csv.unlink(missing_ok=True)
