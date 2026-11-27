import hashlib
import logging
from pathlib import Path
import sqlite3
import sys

from pydantic import BaseModel, ConfigDict, field_validator

from common import fetch_all_pages

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("assignment1_books")

API_URL = "https://openlibrary.org/search.json?q=python&limit=20&fields=title,author_name,first_publish_year"
DB_PATH = Path(__file__).resolve().parent / "books.db"
MAX_PAGES = 1


class Book(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    author: str = "Unknown"
    publication_year: int | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be empty.")
        return value


def parse_book_record(raw: dict, record_num: int = 1) -> Book | None:
    """Safely map raw API payload fields to the Book model."""
    authors_raw = raw.get("author_name")
    if isinstance(authors_raw, list) and authors_raw:
        author_str = ", ".join(str(a).strip() for a in authors_raw if str(a).strip()) or "Unknown"
    elif isinstance(authors_raw, str) and authors_raw.strip():
        author_str = authors_raw.strip()
    else:
        author_str = "Unknown"

    year_raw = raw.get("first_publish_year")
    pub_year: int | None = None
    if year_raw is not None:
        try:
            pub_year = int(year_raw)
        except (ValueError, TypeError):
            pub_year = None

    try:
        return Book(
            title=str(raw.get("title") or ""),
            author=author_str,
            publication_year=pub_year,
        )
    except Exception as err:
        reason = err.errors()[0]["msg"] if hasattr(err, "errors") else "invalid book data"
        logger.warning("Record %d skipped: %s", record_num, reason)
        return None


def compute_book_hash(title: str, author: str) -> str:
    """Generate SHA-256 hash of lowercased title and author to prevent duplicates."""
    combined = f"{title.lower()}{author.lower()}".encode("utf-8")
    return hashlib.sha256(combined).hexdigest()


def save_books_to_sqlite(books: list[Book], db_path: Path) -> tuple[int, int]:
    """Store validated books in SQLite using INSERT OR IGNORE and report counts."""
    inserted = 0
    skipped = 0

    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS books (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    author TEXT,
                    publication_year INTEGER,
                    row_hash TEXT NOT NULL UNIQUE
                )
            """)

            for book in books:
                row_hash = compute_book_hash(book.title, book.author)
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO books (title, author, publication_year, row_hash)
                    VALUES (?, ?, ?, ?)
                    """,
                    (book.title, book.author, book.publication_year, row_hash),
                )
                if cursor.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1

    except sqlite3.Error as err:
        logger.error("Database operation failed: %s", err)
        raise

    return inserted, skipped


def fetch_stored_books(db_path: Path) -> list[tuple]:
    """Retrieve stored books directly from the SQLite database."""
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, title, author, publication_year FROM books ORDER BY id")
            return cursor.fetchall()
    except sqlite3.Error as err:
        logger.error("Failed to read books from database: %s", err)
        raise


def print_books_table(rows: list[tuple]) -> None:
    """Print database rows in a clean aligned table."""
    if not rows:
        print("No books found in the database.")
        return

    headers = ("ID", "Title", "Author", "Year")
    formatted_rows = [
        (
            str(r[0]),
            (r[1][:45] + "...") if len(r[1]) > 48 else r[1],
            (r[2][:30] + "...") if r[2] and len(r[2]) > 33 else (r[2] or "Unknown"),
            str(r[3]) if r[3] is not None else "N/A",
        )
        for r in rows
    ]

    col_widths = [
        max(len(h), max(len(row[i]) for row in formatted_rows))
        for i, h in enumerate(headers)
    ]

    header_line = " | ".join(h.ljust(w) for h, w in zip(headers, col_widths))
    divider_line = "-+-".join("-" * w for w in col_widths)

    print()
    print(header_line)
    print(divider_line)
    for row in formatted_rows:
        print(" | ".join(val.ljust(w) for val, w in zip(row, col_widths)))
    print()


def main() -> None:
    logger.info("Starting Open Library books fetch...")
    try:
        # Fetch first page (20 books) from Open Library API
        raw_records = fetch_all_pages(API_URL, max_pages=MAX_PAGES, page_param="page")
    except Exception as err:
        logger.error("Failed to fetch books from API: %s", err)
        sys.exit(1)

    validated_books: list[Book] = []
    for idx, raw in enumerate(raw_records, start=1):
        book = parse_book_record(raw, idx)
        if book:
            validated_books.append(book)

    logger.info("Fetched %d raw records, validated %d books.", len(raw_records), len(validated_books))

    try:
        inserted, skipped = save_books_to_sqlite(validated_books, DB_PATH)
        logger.info("Database sync complete: %d inserted, %d skipped as duplicate.", inserted, skipped)
        stored_rows = fetch_stored_books(DB_PATH)
        print_books_table(stored_rows)
    except Exception as err:
        logger.error("Failed during database processing: %s", err)
        sys.exit(1)


if __name__ == "__main__":
    main()
