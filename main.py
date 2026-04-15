"""Extract transactions from bank statement PDFs into MongoDB.

Automatically detects the statement format and uses the appropriate parser.
To add a new bank/card format, create a parser in parsers/ and register it
in parsers/__init__.py.

Usage:
    python extract_transactions.py <statements_folder>
    python extract_transactions.py <single_statement.pdf>
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from parsers import detect_parser

load_dotenv()

MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "personal_finance"
COLLECTION_NAME = "transactions"
DUPLICATES_COLLECTION = "duplicate_transactions"
FILE_STATUS_COLLECTION = "file_status"

UNIQUE_INDEX_FIELDS = ["transactionDate", "merchant", "amount", "account", "note"]


def collect_pdfs(path: Path) -> list[Path]:
    """Return a list of PDF files from a file path or directory."""
    if path.is_file() and path.suffix.lower() == ".pdf":
        return [path]
    if path.is_dir():
        return sorted(path.glob("*.pdf"))
    return []


def ensure_indexes(collection):
    """Create the compound unique index if it doesn't already exist."""
    collection.create_index(
        [(field, 1) for field in UNIQUE_INDEX_FIELDS],
        unique=True,
    )


def insert_transactions(collection, dup_collection, transactions, file_name):
    """Insert transactions, saving duplicates to a separate collection.

    Returns (inserted_count, skipped_count).
    """
    now = datetime.now(timezone.utc)
    inserted = 0
    skipped = 0
    for t in transactions:
        t["createdAt"] = now
        try:
            collection.insert_one(t)
            inserted += 1
        except DuplicateKeyError:
            dup_collection.insert_one({**t, "fileName": file_name})
            skipped += 1
    return inserted, skipped


def save_file_status(collection, file_name, account, status, error=None):
    """Record the processing result for a PDF file."""
    doc = {
        "fileName": file_name,
        "account": account,
        "status": status,
        "processedAt": datetime.now(timezone.utc),
    }
    if error:
        doc["error"] = error
    collection.insert_one(doc)


def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <statement.pdf | statements_folder>")
        sys.exit(1)

    cardholder_name = os.environ.get("CARDHOLDER_NAME")
    if not cardholder_name:
        print("Error: CARDHOLDER_NAME not set. Add it to .env or set it as an environment variable.")
        sys.exit(1)

    target = Path(sys.argv[1])
    pdfs = collect_pdfs(target)
    if not pdfs:
        print(f"No PDF files found at: {target}")
        sys.exit(1)

    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    collection = db[COLLECTION_NAME]
    dup_collection = db[DUPLICATES_COLLECTION]
    status_collection = db[FILE_STATUS_COLLECTION]
    ensure_indexes(collection)

    total_inserted = 0
    total_skipped = 0

    for pdf_path in pdfs:
        try:
            parser = detect_parser(str(pdf_path), cardholder_name)
        except ValueError as e:
            print(f"Skipping {pdf_path.name}: {e}")
            save_file_status(status_collection, pdf_path.name, "", "failed", str(e))
            continue

        try:
            transactions = parser.parse(str(pdf_path))
            inserted, skipped = insert_transactions(
                collection, dup_collection, transactions, pdf_path.name
            )

            total_inserted += inserted
            total_skipped += skipped

            print(f"{pdf_path.name}: {inserted} inserted, {skipped} skipped (duplicates)")
            save_file_status(status_collection, pdf_path.name, parser.ACCOUNT, "done")
        except Exception as e:
            print(f"Error processing {pdf_path.name}: {e}")
            save_file_status(
                status_collection, pdf_path.name, parser.ACCOUNT, "failed", str(e)
            )

    print(f"\nTotal: {total_inserted} inserted, {total_skipped} skipped across {len(pdfs)} file(s)")

    client.close()


if __name__ == "__main__":
    main()
