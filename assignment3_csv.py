import csv
import hashlib
import logging
from pathlib import Path
import sqlite3
import sys
import unicodedata

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("assignment3_csv")

CSV_PATH = Path(__file__).resolve().parent / "users.csv"
DB_PATH = Path(__file__).resolve().parent / "users.db"
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_ROW_COUNT = 10000

DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class User(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    email: EmailStr

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        # Strip control characters (Unicode category 'C')
        filtered = "".join(ch for ch in value if not unicodedata.category(ch).startswith("C"))
        cleaned = filtered.strip()
        if not cleaned:
            raise ValueError("Name cannot be empty.")
        if any(cleaned.startswith(p) for p in DANGEROUS_PREFIXES):
            raise ValueError("formula injection detected")
        if len(cleaned) > 100:
            raise ValueError("name exceeds 100 characters")
        return cleaned

    @field_validator("email", mode="before")
    @classmethod
    def clean_email(cls, value: object) -> str:
        # Pre-clean string before EmailStr validation
        if not isinstance(value, str):
            raise ValueError("Email must be a string.")
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Email cannot be empty.")
        if any(cleaned.startswith(p) for p in DANGEROUS_PREFIXES):
            raise ValueError("formula injection detected")
        return cleaned


def compute_file_hash(filepath: Path) -> str:
    """Compute SHA-256 hash of the CSV file."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def detect_csv_delimiter(filepath: Path) -> str:
    """Detect CSV delimiter using csv.Sniffer, defaulting to comma on error."""
    with open(filepath, "r", encoding="utf-8") as f:
        sample = f.read(2048)
        if not sample.strip():
            print("Error: CSV file is empty.")
            sys.exit(1)
        try:
            dialect = csv.Sniffer().sniff(sample)
            return dialect.delimiter
        except csv.Error:
            return ","


def parse_and_validate_csv(filepath: Path, delimiter: str) -> tuple[list[User], int, int]:
    """Read CSV, validate each record with Pydantic, and collect metrics."""
    valid_users: list[User] = []
    total_rows = 0
    skipped_invalid = 0

    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        if not reader.fieldnames:
            print("Error: CSV file has no header row.")
            sys.exit(1)

        field_map = {fn.strip().lower(): fn for fn in reader.fieldnames if fn}
        if "name" not in field_map or "email" not in field_map:
            print("Error: CSV must contain 'name' and 'email' columns.")
            sys.exit(1)

        name_col = field_map["name"]
        email_col = field_map["email"]

        expected_line = 2
        for row in reader:
            actual_line = reader.line_num

            # Detect completely blank lines skipped by the low-level CSV parser
            while expected_line < actual_line:
                logger.warning("Row %d skipped: completely blank line.", expected_line)
                total_rows += 1
                skipped_invalid += 1
                expected_line += 1

            total_rows += 1
            expected_line = actual_line + 1

            if total_rows > MAX_ROW_COUNT:
                print(f"Error: CSV file exceeds the limit of {MAX_ROW_COUNT} rows.")
                sys.exit(1)

            # Check for empty/blank values in row
            if not any(val and val.strip() for val in row.values()):
                logger.warning("Row %d skipped: completely blank row.", actual_line)
                skipped_invalid += 1
                continue

            raw_name = row.get(name_col)
            raw_email = row.get(email_col)

            try:
                user = User(name=raw_name, email=raw_email)
                valid_users.append(user)
            except Exception as err:
                reason = err.errors()[0]["msg"] if hasattr(err, "errors") else "invalid record"
                if "formula" in reason.lower():
                    short_reason = "disallowed formula character in name or email"
                elif "email" in reason.lower() or "valid email" in reason.lower():
                    short_reason = "invalid email"
                elif "100 characters" in reason.lower():
                    short_reason = "name exceeds 100 characters"
                elif "name" in reason.lower():
                    short_reason = "invalid name"
                else:
                    short_reason = "invalid data"

                logger.warning("Row %d skipped: %s", actual_line, short_reason)
                skipped_invalid += 1

    return valid_users, total_rows, skipped_invalid


def is_file_already_imported(db_path: Path, file_hash: str) -> bool:
    """Check if the given file hash already exists in import_log."""
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS import_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_hash TEXT NOT NULL UNIQUE,
                    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("SELECT imported_at FROM import_log WHERE file_hash = ?", (file_hash,))
            return cursor.fetchone() is not None
    except sqlite3.Error as err:
        logger.error("Failed to check import log: %s", err)
        raise


def import_users_to_database(
    db_path: Path,
    users: list[User],
    file_hash: str,
) -> tuple[int, int]:
    """
    Import users into SQLite in a single transaction with duplicate checks.
    Returns (inserted_count, skipped_duplicate_count).
    """
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()

            # Ensure tables exist
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS import_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_hash TEXT NOT NULL UNIQUE,
                    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE
                )
            """)

            # Single transaction batch insert
            changes_before = conn.total_changes
            cursor.executemany(
                "INSERT OR IGNORE INTO users (name, email) VALUES (?, ?)",
                [(u.name, u.email) for u in users],
            )
            inserted_count = conn.total_changes - changes_before
            skipped_duplicate = len(users) - inserted_count

            # Record file import hash
            cursor.execute("INSERT INTO import_log (file_hash) VALUES (?)", (file_hash,))

            return inserted_count, skipped_duplicate

    except sqlite3.Error as err:
        logger.error("Database operation failed: %s", err)
        raise


def fetch_stored_users(db_path: Path) -> list[tuple]:
    """Retrieve all stored users from the database."""
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, email FROM users ORDER BY id")
            return cursor.fetchall()
    except sqlite3.Error as err:
        logger.error("Failed to query users database: %s", err)
        raise


def print_users_table(rows: list[tuple]) -> None:
    """Print database rows in a clean aligned table."""
    if not rows:
        print("No users found in database.")
        return

    headers = ("ID", "Name", "Email")
    formatted_rows = [
        (str(r[0]), str(r[1]), str(r[2]))
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
    if not CSV_PATH.exists():
        print(f"Error: CSV file not found at {CSV_PATH}")
        sys.exit(1)

    if CSV_PATH.stat().st_size > MAX_FILE_SIZE_BYTES:
        print(f"Error: CSV file exceeds the 5 MB limit ({CSV_PATH.stat().st_size} bytes).")
        sys.exit(1)

    try:
        file_hash = compute_file_hash(CSV_PATH)
        if is_file_already_imported(DB_PATH, file_hash):
            print("this file was already imported")
            sys.exit(0)

        delimiter = detect_csv_delimiter(CSV_PATH)
        valid_users, total_read, skipped_invalid = parse_and_validate_csv(CSV_PATH, delimiter)

        inserted, skipped_duplicate = import_users_to_database(
            DB_PATH,
            valid_users,
            file_hash,
        )

        logger.info(
            "Import summary: total rows read=%d, inserted=%d, skipped as invalid=%d, skipped as duplicate=%d",
            total_read,
            inserted,
            skipped_invalid,
            skipped_duplicate,
        )

        stored_users = fetch_stored_users(DB_PATH)
        print_users_table(stored_users)

    except Exception as err:
        logger.error("CSV import encountered an error: %s", err)
        sys.exit(1)


if __name__ == "__main__":
    main()
