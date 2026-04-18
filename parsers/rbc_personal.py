"""Shared parser for RBC personal account statements (chequing, savings, etc.).

These statements share the same PDF layout — Date, Description, Withdrawals,
Deposits, Balance columns — but pdfplumber can't detect table structures in
them, so we use word-level extraction with x-position column classification.

Parser hierarchy:

    StatementParser (ABC)                   — base.py
    ├── BMOChequingParser                   — bmo_chequing.py
    ├── RBCMasterCardParser                 — rbc_mastercard.py
    ├── RBCPersonalParser                   — rbc_personal.py (this file)
    │   ├── RBCChequingParser               — rbc_chequing.py
    │   └── RBCSavingsParser                — rbc_savings.py
    ├── RBCInvestmentParser                 — rbc_investment.py
    │   ├── RBCTFSAParser                   — rbc_tfsa.py
    │   └── RBCRRSPParser                   — rbc_rrsp.py
    ├── WealthSimpleParser                  — ws.py
    └── WealthSimplePDFParser               — ws_pdf.py
"""

import re
from datetime import datetime

import pdfplumber

from .base import StatementParser

# Handles optional missing spaces: "FromFebruary13,2026toMarch13,2026"
_STATEMENT_PERIOD_RE = re.compile(
    r"From\s*([A-Za-z]+)\s*(\d{1,2})\s*,\s*(\d{4})\s*to\s*([A-Za-z]+)\s*(\d{1,2})\s*,\s*(\d{4})"
)

_BASE_FEATURES = [
    ("RBC", "Missing RBC branding"),
    ("account statement", "Missing 'account statement' header"),
    ("Details of your account activity", "Missing account activity section"),
    ("Withdrawals($)", "Missing withdrawals column"),
    ("Deposits($)", "Missing deposits column"),
]

_AMOUNT_RE = re.compile(r"^[\d,]+\.\d{2}$")
_DATE_RE = re.compile(r"^\d{1,2}[A-Z][a-z]{2}$")

_SKIP_DESCRIPTIONS = {"OpeningBalance", "ClosingBalance"}


class RBCPersonalParser(StatementParser):
    """Base parser for RBC personal account statements.

    Subclasses must define:
        ACCOUNT: str — e.g. "RBC Chequing"
        _MATCH_KEYWORD: str — e.g. "personalbanking" (no spaces, lowercased)
        _ACCOUNT_TYPE_FEATURE: tuple — e.g. ("personal banking", "Missing ...")
    """

    ACCOUNT: str
    _MATCH_KEYWORD: str
    _ACCOUNT_TYPE_FEATURE: tuple

    @classmethod
    def matches(cls, first_page_text: str) -> bool:
        text = first_page_text.lower().replace(" ", "")
        return "rbc" in text and cls._MATCH_KEYWORD in text and "accountstatement" in text

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
            start, end = self._extract_period(pdf)
        return f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"

    def parse(self, pdf_path: str) -> list[dict]:
        transactions = []
        with pdfplumber.open(pdf_path) as pdf:
            start_date, end_date = self._extract_period(pdf)
            for page in pdf.pages:
                col_bounds = self._find_columns(page)
                if not col_bounds:
                    continue
                transactions.extend(
                    self._parse_page(page, col_bounds, start_date, end_date)
                )
        period = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
        return self._attach_source(transactions, pdf_path, period)

    @staticmethod
    def _find_columns(page):
        """Find x-boundaries for each column from the header row."""
        words = page.extract_words()
        cols = {}
        for w in words:
            text = w["text"]
            if text == "Withdrawals($)":
                cols["withdrawal_x0"] = w["x0"]
                cols["withdrawal_x1"] = w["x1"]
            elif text == "Deposits($)":
                cols["deposit_x0"] = w["x0"]
                cols["deposit_x1"] = w["x1"]
            elif text == "Balance($)":
                cols["balance_x0"] = w["x0"]
            elif text == "Date":
                cols["date_x0"] = w["x0"]
                cols["header_top"] = w["top"]
        if "withdrawal_x0" not in cols or "deposit_x0" not in cols:
            return None
        # Midpoints between columns for classifying amounts
        cols["wd_mid"] = (cols["withdrawal_x1"] + cols["deposit_x0"]) / 2
        cols["db_mid"] = (cols["deposit_x1"] + cols["balance_x0"]) / 2
        return cols

    def _parse_page(self, page, cols, start_date, end_date):
        words = page.extract_words()
        lines = self._group_by_line(words, cols["header_top"])

        transactions = []
        current_date_str = None
        description_parts = []

        for line in lines:
            desc_words = []
            withdrawal = None
            deposit = None

            for w in line:
                x0 = w["x0"]
                text = w["text"]

                # Skip sidebar/margin text left of the date column
                if x0 < cols["date_x0"] - 5:
                    continue

                if x0 < cols["withdrawal_x0"] - 10:
                    # Date or description column
                    if _DATE_RE.match(text):
                        current_date_str = text
                    else:
                        desc_words.append(text)
                elif x0 < cols["wd_mid"] and _AMOUNT_RE.match(text):
                    withdrawal = text
                elif x0 < cols["db_mid"] and _AMOUNT_RE.match(text):
                    deposit = text
                # else: balance column — skip

            desc = " ".join(desc_words)
            if desc and desc not in _SKIP_DESCRIPTIONS:
                description_parts.append(desc)

            if (withdrawal or deposit) and current_date_str and description_parts:
                amount = (
                    -self._parse_amount(withdrawal)
                    if withdrawal
                    else self._parse_amount(deposit)
                )
                transactions.append({
                    "transactionDate": self._parse_date(
                        current_date_str, start_date, end_date
                    ),
                    "merchant": " ".join(description_parts),
                    "amount": amount,
                    "account": self.ACCOUNT,
                    "type": "withdrawal" if withdrawal else "deposit",
                    "note": "",
                })
                description_parts = []

        return transactions

    @staticmethod
    def _group_by_line(words, header_top, tolerance=2):
        """Group words below the header into lines by y-position."""
        lines_dict = {}
        for w in words:
            if w["top"] <= header_top:
                continue
            y_key = round(w["top"] / tolerance) * tolerance
            lines_dict.setdefault(y_key, []).append(w)

        return [
            sorted(ws, key=lambda w: w["x0"])
            for _, ws in sorted(lines_dict.items())
        ]

    @staticmethod
    def _extract_period(pdf):
        text = pdf.pages[0].extract_text()
        m = _STATEMENT_PERIOD_RE.search(text)
        if m:
            start = datetime.strptime(
                f"{m.group(1)} {m.group(2)}, {m.group(3)}", "%B %d, %Y"
            )
            end = datetime.strptime(
                f"{m.group(4)} {m.group(5)}, {m.group(6)}", "%B %d, %Y"
            )
            return start, end
        raise ValueError("Could not find statement period in PDF")

    @staticmethod
    def _parse_date(date_str, start_date, end_date):
        """Parse dates like '16Feb' using the statement period to determine year.

        Parses with year included to handle leap day (Feb 29) correctly.
        """
        m = re.match(r"(\d{1,2})([A-Z][a-z]{2})", date_str)
        if not m:
            raise ValueError(f"Cannot parse date: {date_str}")
        day_month_str = f"{m.group(1)} {m.group(2)}"
        for year in (end_date.year, start_date.year):
            try:
                candidate = datetime.strptime(f"{day_month_str} {year}", "%d %b %Y")
            except ValueError:
                continue
            if start_date <= candidate <= end_date:
                return candidate
        return datetime.strptime(f"{day_month_str} {end_date.year}", "%d %b %Y")

    @staticmethod
    def _parse_amount(s):
        return float(s.replace(",", ""))
