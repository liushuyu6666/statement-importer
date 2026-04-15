"""Shared parser for RBC investment statements (Royal Mutual Funds Inc.).

Handles TFSA, RRSP, and other registered account statements that share
the same PDF layout with fund-level transaction activity.

Parser hierarchy:

    StatementParser (ABC)                   — base.py
    ├── RBCMasterCardParser                 — rbc_mastercard.py
    ├── RBCPersonalParser                   — rbc_personal.py
    │   ├── RBCChequingParser               — rbc_chequing.py
    │   └── RBCSavingsParser                — rbc_savings.py
    └── RBCInvestmentParser                 — rbc_investment.py (this file)
        ├── RBCTFSAParser                   — rbc_tfsa.py
        └── RBCRRSPParser                   — rbc_rrsp.py
"""

import re
from datetime import datetime

import pdfplumber

from .base import StatementParser

# e.g. "October 1, 2024 to December 31, 2024"
_PERIOD_RE = re.compile(
    r"([A-Z][a-z]+)\s*(\d{1,2})\s*,\s*(\d{4})\s*to\s*"
    r"([A-Z][a-z]+)\s*(\d{1,2})\s*,\s*(\d{4})"
)

_BASE_FEATURES = [
    ("Your investment activity", "Missing investment activity section"),
]

# Fund header: "RBC Canadian Money Market Fund- Sr. A (RBF271)"
_FUND_HEADER_RE = re.compile(r"^(RBC\s*.+\(RBF\d+\))")

# Matches lines starting with a date and containing a 2-decimal amount.
# The type is validated against _KNOWN_TYPES to skip non-transaction rows
# (Opening/Closing Balance, Income Record Date Holdings).
_TRANSACTION_RE = re.compile(
    r"^([A-Z][a-z]{2}\s*\d{2}\s*\d{4})\s+"  # date: Mon DD YYYY
    r"(.+?)\s+"                               # transaction type
    r"(-?[\d,]+\.\d{2})"                      # amount of transaction (2 decimals)
)

_ACTIVITY_SECTION = "Your investment activity"

# Transaction types that have a real amount and should be saved.
# Normalized (no spaces, lowercase) for matching against pdfplumber text.
_KNOWN_TYPES = {
    "contribution",
    "investmentswitch",
    "incomereinvested",
    "returnofcapital",
}


class RBCInvestmentParser(StatementParser):
    """Base parser for RBC investment statements.

    Subclasses must define:
        ACCOUNT: str — e.g. "RBC TFSA"
        _MATCH_KEYWORD: str — e.g. "tax-freesavingsaccount" (no spaces, lowercased)
        _ACCOUNT_TYPE_FEATURE: tuple — e.g. ("Tax-Free Savings Account", "Missing ...")
    """

    ACCOUNT: str
    _MATCH_KEYWORD: str
    _ACCOUNT_TYPE_FEATURE: tuple

    @classmethod
    def matches(cls, first_page_text: str) -> bool:
        text = first_page_text.lower().replace(" ", "")
        return "investmentstatement" in text and cls._MATCH_KEYWORD in text

    @classmethod
    def validate(cls, full_text: str, cardholder_name: str) -> list[str]:
        features = _BASE_FEATURES + [cls._ACCOUNT_TYPE_FEATURE]
        normalized = full_text.replace(" ", "")
        errors = [
            msg for feature, msg in features
            if feature.replace(" ", "") not in normalized
        ]
        if cardholder_name.upper().replace(" ", "") not in normalized.upper():
            errors.append(f"Cardholder name '{cardholder_name}' not found in statement")
        return errors

    def get_period(self, pdf_path: str) -> str:
        with pdfplumber.open(pdf_path) as pdf:
            text = pdf.pages[0].extract_text() or ""
        m = _PERIOD_RE.search(text)
        if not m:
            raise ValueError("Could not find statement period in PDF")
        start = datetime.strptime(
            f"{m.group(1)} {m.group(2)}, {m.group(3)}", "%B %d, %Y"
        )
        end = datetime.strptime(
            f"{m.group(4)} {m.group(5)}, {m.group(6)}", "%B %d, %Y"
        )
        return f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"

    def parse(self, pdf_path: str) -> list[dict]:
        transactions = []
        current_fund = None
        in_activity = False

        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                for line in text.split("\n"):
                    line = line.strip()

                    if _ACTIVITY_SECTION in line:
                        in_activity = True
                        continue

                    if not in_activity:
                        continue

                    # Track current fund
                    fund_match = _FUND_HEADER_RE.match(line)
                    if fund_match:
                        current_fund = fund_match.group(1)
                        continue

                    # Match transactions that have an amount
                    txn_match = _TRANSACTION_RE.match(line)
                    if txn_match and current_fund:
                        raw_type = txn_match.group(2).strip()
                        if raw_type.replace(" ", "").lower() not in _KNOWN_TYPES:
                            continue
                        transactions.append({
                            "transactionDate": self._parse_date(txn_match.group(1)),
                            "merchant": current_fund,
                            "amount": self._parse_amount(txn_match.group(3)),
                            "account": self.ACCOUNT,
                            "note": raw_type,
                        })

        return transactions

    @staticmethod
    def _parse_date(date_str: str) -> datetime:
        """Parse dates like 'Jan 06 2025' or 'Jan062025' (missing spaces)."""
        cleaned = date_str.replace(" ", "")
        # Always 3 letters + 2 digits + 4 digits
        return datetime.strptime(f"{cleaned[:3]} {cleaned[3:5]} {cleaned[5:]}", "%b %d %Y")

    @staticmethod
    def _parse_amount(s: str) -> float:
        return float(s.replace(",", ""))
