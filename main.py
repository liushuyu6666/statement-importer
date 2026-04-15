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

from parsers import detect_parser

load_dotenv()

MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "personal_finance"
COLLECTION_NAME = "transactions"
FILE_STATUS_COLLECTION = "file_status"


def collect_pdfs(path: Path) -> list[Path]:
    """Return a list of PDF files from a file path or directory."""
    if path.is_file() and path.suffix.lower() == ".pdf":
        return [path]
    if path.is_dir():
        return sorted(path.glob("*.pdf"))
    return []


def ensure_file_status_index(collection):
    """Create a unique index on (account, period) for statement-level dedup."""
    collection.create_index(
        [("account", 1), ("period", 1)],
        unique=True,
    )


def drop_legacy_indexes(collection):
    """Remove the old transaction-level unique index if it exists."""
    try:
        collection.drop_index("transactionDate_1_merchant_1_amount_1_account_1_note_1")
    except Exception:
        pass


def insert_transactions(collection, transactions):
    """Insert transactions into the collection. Returns inserted count."""
    now = datetime.now(timezone.utc)
    for t in transactions:
        t["createdAt"] = now
    if transactions:
        collection.insert_many(transactions)
    return len(transactions)


def is_already_processed(status_collection, account, period):
    """Check if a statement with this account+period was already processed."""
    return status_collection.find_one({
        "account": account,
        "period": period,
        "status": "done",
    }) is not None


def save_file_status(collection, file_name, account, period, status, error=None):
    """Record the processing result for a PDF file.

    Uses upsert so a retry after failure updates the existing entry.
    """
    doc = {
        "fileName": file_name,
        "account": account,
        "period": period,
        "status": status,
        "processedAt": datetime.now(timezone.utc),
    }
    if error:
        doc["error"] = error
    collection.update_one(
        {"account": account, "period": period},
        {"$set": doc},
        upsert=True,
    )


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
    status_collection = db[FILE_STATUS_COLLECTION]
    drop_legacy_indexes(collection)
    ensure_file_status_index(status_collection)

    total_inserted = 0
    total_skipped = 0

    for pdf_path in pdfs:
        try:
            parser = detect_parser(str(pdf_path), cardholder_name)
            period = parser.get_period(str(pdf_path))
        except ValueError as e:
            print(f"Skipping {pdf_path.name}: {e}")
            save_file_status(status_collection, pdf_path.name, "", "", "failed", str(e))
            continue

        if is_already_processed(status_collection, parser.ACCOUNT, period):
            print(f"Skipping {pdf_path.name}: already processed ({parser.ACCOUNT}, {period})")
            total_skipped += 1
            continue

        try:
            transactions = parser.parse(str(pdf_path))
            inserted = insert_transactions(collection, transactions)

            total_inserted += inserted

            print(f"{pdf_path.name}: {inserted} transactions inserted")
            save_file_status(
                status_collection, pdf_path.name, parser.ACCOUNT, period, "done"
            )
        except Exception as e:
            print(f"Error processing {pdf_path.name}: {e}")
            save_file_status(
                status_collection, pdf_path.name, parser.ACCOUNT, period, "failed", str(e)
            )

    print(f"\nTotal: {total_inserted} inserted, {total_skipped} skipped across {len(pdfs)} file(s)")

    client.close()


if __name__ == "__main__":
    main()
