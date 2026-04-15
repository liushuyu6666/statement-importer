import re
from datetime import datetime

import pdfplumber

from .base import StatementParser

# Matches lines like: FEB 10 FEB 12 DESCRIPTION $17.69  or  -$1,000.00
# No end-of-line anchor — page 1 has sidebar text appended to some rows.
_TRANSACTION_RE = re.compile(
    r"^([A-Z]{3} \d{2})\s+"  # transaction date
    r"[A-Z]{3} \d{2}\s+"     # posting date (skipped)
    r"(.+?)\s+"               # activity description (non-greedy)
    r"(-?\$[\d,]+\.\d{2})"   # amount (first match)
)

# Extracts the statement period year, e.g. "STATEMENT FROM FEB 11 TO MAR 10, 2026"
_STATEMENT_PERIOD_RE = re.compile(
    r"STATEMENT FROM .+ TO .+,\s*(\d{4})"
)

# Structural features expected in a valid RBC MasterCard statement.
_REQUIRED_FEATURES = [
    ("RBC", "Missing RBC branding"),
    ("Cash Back Mastercard", "Missing 'Cash Back Mastercard' header"),
    ("STATEMENT FROM", "Missing statement period header"),
    ("PREVIOUS ACCOUNT BALANCE", "Missing previous account balance"),
    ("TOTAL ACCOUNT BALANCE", "Missing total account balance"),
    ("ACTIVITY DESCRIPTION", "Missing transaction table header"),
    ("AMOUNT ($)", "Missing amount column header"),
]


class RBCMasterCardParser(StatementParser):
    ACCOUNT = "RBC MasterCard"

    @staticmethod
    def matches(first_page_text: str) -> bool:
        return "RBC" in first_page_text and "Cash Back Mastercard" in first_page_text

    @staticmethod
    def validate(full_text: str, cardholder_name: str) -> list[str]:
        errors = [msg for feature, msg in _REQUIRED_FEATURES if feature not in full_text]
        if cardholder_name.upper() not in full_text.upper():
            errors.append(f"Cardholder name '{cardholder_name}' not found in statement")
        return errors

    def parse(self, pdf_path: str) -> list[dict]:
        transactions = []
        with pdfplumber.open(pdf_path) as pdf:
            year = self._extract_year(pdf)
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue
                for line in text.split("\n"):
                    m = _TRANSACTION_RE.match(line.strip())
                    if m:
                        transactions.append({
                            "transactionDate": self._parse_date(m.group(1), year),
                            "merchant": m.group(2).strip(),
                            "amount": self._parse_amount(m.group(3)),
                            "account": self.ACCOUNT,
                            "note": "",
                        })
        return transactions

    @staticmethod
    def _extract_year(pdf) -> int:
        text = pdf.pages[0].extract_text()
        m = _STATEMENT_PERIOD_RE.search(text)
        if m:
            return int(m.group(1))
        raise ValueError("Could not find statement year in PDF")

    @staticmethod
    def _parse_date(date_str: str, year: int) -> datetime:
        return datetime.strptime(f"{date_str} {year}", "%b %d %Y")

    @staticmethod
    def _parse_amount(s: str) -> float:
        return -float(s.replace("$", "").replace(",", ""))
