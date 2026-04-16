"""Parser for WealthSimple monthly statement CSVs.

Handles all WealthSimple account types (Non-registered, Cash, FHSA, Crypto,
TFSA, RRSP, etc.) since they share the same CSV format.

Filename pattern:
    {AccountType}-monthly-statement-transactions-{AccountID}{Currency}-{Date}.csv
    e.g. Non-registered-monthly-statement-transactions-HQ0JF1Q08CAD-2026-03-01.csv

CSV columns: date, transaction, description, amount, balance, currency

Parser hierarchy:

    StatementParser (ABC)                   — base.py
    ├── BMOChequingParser                   — bmo_chequing.py
    ├── RBCMasterCardParser                 — rbc_mastercard.py
    ├── RBCPersonalParser                   — rbc_personal.py
    │   ├── RBCChequingParser               — rbc_chequing.py
    │   └── RBCSavingsParser                — rbc_savings.py
    ├── RBCInvestmentParser                 — rbc_investment.py
    │   ├── RBCTFSAParser                   — rbc_tfsa.py
    │   └── RBCRRSPParser                   — rbc_rrsp.py
    └── WealthSimpleParser                  — ws.py (this file)
"""

import csv
import re
from datetime import datetime
from pathlib import Path

from .base import StatementParser
from .ws_common import ws_account_name

# e.g. "Non-registered-monthly-statement-transactions-HQ0JF1Q08CAD-2026-03-01.csv"
_FILENAME_RE = re.compile(
    r"^.+?-monthly-statement-transactions-(\w+)-(\d{4}-\d{2}-\d{2})\.csv$"
)

_WS_HEADERS = {"date", "transaction", "description", "amount", "balance", "currency"}


class WealthSimpleParser(StatementParser):

    def __init__(self, account_no: str):
        self.ACCOUNT = ws_account_name(account_no)

    @staticmethod
    def matches(first_line: str) -> bool:
        headers = {h.strip().strip('"') for h in first_line.split(",")}
        return _WS_HEADERS.issubset(headers)

    @staticmethod
    def validate(full_text: str, cardholder_name: str) -> list[str]:
        return []

    @staticmethod
    def extract_account_no(file_path: str) -> str:
        m = _FILENAME_RE.match(Path(file_path).name)
        if not m:
            raise ValueError(
                f"Cannot extract account no from filename: {Path(file_path).name}"
            )
        return m.group(1)

    def get_period(self, file_path: str) -> str:
        m = _FILENAME_RE.match(Path(file_path).name)
        if not m:
            raise ValueError(
                f"Cannot extract period from filename: {Path(file_path).name}"
            )
        # "2026-03-01" → "2026-03"
        return m.group(2)[:7]

    def parse(self, file_path: str) -> list[dict]:
        transactions = []
        with open(file_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                merchant, note = self._split_description(row["description"])
                transactions.append({
                    "transactionDate": datetime.strptime(row["date"], "%Y-%m-%d"),
                    "merchant": merchant,
                    "amount": float(row["amount"]),
                    "account": self.ACCOUNT,
                    "type": row["transaction"].lower(),
                    "note": note,
                })
        return transactions

    @staticmethod
    def _split_description(desc: str) -> tuple[str, str]:
        """Split 'TICKER - Name: details' into (ticker, details).

        For descriptions without a ticker (NRT, WD, INT), the full
        description becomes the merchant and note is empty.
        """
        parts = desc.split(": ", 1)
        if len(parts) == 2:
            return parts[0].strip(), parts[1].strip()
        return desc.strip(), ""
