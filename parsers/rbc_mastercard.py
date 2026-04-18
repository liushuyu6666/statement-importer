import re
from datetime import datetime

import pdfplumber

from .base import StatementParser

# Matches lines like: FEB 10 FEB 12 DESCRIPTION $17.69  or  -$1,000.00
# Handles missing spaces in dates: "FEB10 FEB12" or "FEB 10 FEB 12"
# No end-of-line anchor — page 1 has sidebar text appended to some rows.
_TRANSACTION_RE = re.compile(
    r"^([A-Z]{3}\s*\d{2})\s+"   # transaction date: "JAN 10" or "JAN10"
    r"[A-Z]{3}\s*\d{2}\s+"      # posting date (skipped)
    r"(.+?)\s+"                  # activity description (non-greedy)
    r"(-?\$[\d,]+\.\d{2})"      # amount (first match)
)

# Extracts the full statement period — handles optional missing spaces.
# Format 1: "STATEMENT FROM DEC 11 TO JAN 10, 2022" (year only at end)
# Format 2: "STATEMENT FROM DEC 11, 2021 TO JAN 10, 2022" (year after each date)
# Also handles merged text: "STATEMENTFROMDEC11,2021TOJAN10,2022"
_STATEMENT_PERIOD_RE = re.compile(
    r"STATEMENT\s*FROM\s*([A-Z]{3})\s*(\d{1,2})\s*(?:,\s*(\d{4}))?\s*TO\s*([A-Z]{3})\s*(\d{1,2})\s*,\s*(\d{4})"
)

_REQUIRED_FEATURES = [
    ("RBC", "Missing RBC branding"),
    ("Cash Back Mastercard", "Missing 'Cash Back Mastercard' header"),
    ("STATEMENT FROM", "Missing statement period header"),
    ("TRANSACTION", "Missing transaction column header"),
    ("ACTIVITY DESCRIPTION", "Missing activity description column header"),
    ("AMOUNT ($)", "Missing amount column header"),
]


class RBCMasterCardParser(StatementParser):
    ACCOUNT = "RBC MasterCard"

    @staticmethod
    def matches(first_page_text: str) -> bool:
        text = first_page_text.replace(" ", "").lower()
        return "rbc" in text and "cashbackmastercard" in text

    @staticmethod
    def validate(full_text: str, cardholder_name: str) -> list[str]:
        normalized = full_text.replace(" ", "")
        errors = [
            msg for feature, msg in _REQUIRED_FEATURES
            if feature.replace(" ", "") not in normalized
        ]
        if cardholder_name.upper().replace(" ", "") not in normalized.upper():
            errors.append(f"Cardholder name '{cardholder_name}' not found in statement")
        return errors

    def get_period(self, pdf_path: str) -> str:
        with pdfplumber.open(pdf_path) as pdf:
            start, end = self._extract_period(pdf)
        return f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"

    def parse(self, pdf_path: str) -> list[dict]:
        transactions = []
        with pdfplumber.open(pdf_path) as pdf:
            start_date, end_date = self._extract_period(pdf)
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue
                for line in text.split("\n"):
                    m = _TRANSACTION_RE.match(line.strip())
                    if m:
                        amount = self._parse_amount(m.group(3))
                        transactions.append({
                            "transactionDate": self._parse_date(m.group(1), start_date, end_date),
                            "merchant": m.group(2).strip(),
                            "amount": amount,
                            "account": self.ACCOUNT,
                            "type": "payment" if amount > 0 else "purchase",
                            "note": "",
                        })
        return transactions

    @staticmethod
    def _extract_period(pdf) -> tuple[datetime, datetime]:
        text = pdf.pages[0].extract_text()
        m = _STATEMENT_PERIOD_RE.search(text)
        if not m:
            raise ValueError("Could not find statement period in PDF")
        end_month, end_day, end_year = m.group(4), int(m.group(5)), int(m.group(6))
        end_date = datetime.strptime(f"{end_month} {end_day} {end_year}", "%b %d %Y")
        start_month, start_day = m.group(1), int(m.group(2))
        if m.group(3):
            # Explicit start year: "DEC 11, 2021 TO JAN 10, 2022"
            start_date = datetime.strptime(
                f"{start_month} {start_day} {m.group(3)}", "%b %d %Y"
            )
        else:
            # No start year — infer from end year (may be previous year)
            for y in (end_year, end_year - 1):
                start_date = datetime.strptime(
                    f"{start_month} {start_day} {y}", "%b %d %Y"
                )
                if start_date <= end_date:
                    break
        return start_date, end_date

    @staticmethod
    def _parse_date(date_str: str, start_date: datetime, end_date: datetime) -> datetime:
        """Parse dates like 'JAN 10' or 'JAN10' (missing space).

        Statements can span a year boundary (e.g. Dec 11 2025 to Jan 12 2026).
        Pick the year by which end of the period the transaction month matches,
        since some PDFs list transactions a day or two outside the declared
        period — so a strict in-range check isn't reliable.
        """
        cleaned = date_str.replace(" ", "")
        day_month = f"{cleaned[:3]} {cleaned[3:]}"
        probe = datetime.strptime(f"{day_month} 2000", "%b %d %Y")
        if probe.month == start_date.month:
            year = start_date.year
        else:
            year = end_date.year
        return datetime.strptime(f"{day_month} {year}", "%b %d %Y")

    @staticmethod
    def _parse_amount(s: str) -> float:
        return -float(s.replace("$", "").replace(",", ""))
