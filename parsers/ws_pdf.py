"""Parser for WealthSimple PDF monthly statements (ShareOwner format).

Handles all WealthSimple account types that use the ShareOwner PDF format
with "ORDER EXECUTION ONLY ACCOUNT" header.

Filename pattern:
    {AccountID}_{PersonID}_{YYYY-MM}_v_{version}.pdf
    e.g. HQ18N6512CAD_person-007oTlvcyUj5_2021-12_v_0.pdf

PDF columns (Activity section): Date, Transaction, Description,
    Charged ($), Credit ($), Balance ($)

Transaction codes are listed on the last page of each statement
(BUY, SELL, DIV, CONT, DEP, WD, FEE, etc.).

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
    ├── WealthSimpleParser                  — ws.py
    └── WealthSimplePDFParser               — ws_pdf.py (this file)
"""

import re
from datetime import datetime
from pathlib import Path

import pdfplumber

from .base import StatementParser
from .ws_common import ws_account_name

# e.g. "HQ18N6512CAD_person-007oTlvcyUj5_2021-12_v_0.pdf"
_FILENAME_RE = re.compile(r"^([A-Z0-9]+)_.+_(\d{4}-\d{2})_v_\d+\.pdf$")

# Statement period in PDF text: "2021-12-01 - 2021-12-31"
_PERIOD_TEXT_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_AMOUNT_RE = re.compile(r"^\$?[\d,]+\.\d{2}$")


class WealthSimplePDFParser(StatementParser):

    def __init__(self, account_no: str):
        self.ACCOUNT = ws_account_name(account_no)

    @staticmethod
    def matches(first_page_text: str) -> bool:
        text = first_page_text.replace(" ", "").lower()
        return "orderexecutiononlyaccount" in text

    @staticmethod
    def validate(full_text: str, cardholder_name: str) -> list[str]:
        normalized = full_text.replace(" ", "")
        errors = []
        if "Activity-Currentperiod" not in normalized:
            errors.append("Missing 'Activity - Current period' section")
        return errors

    @staticmethod
    def extract_account_id(file_path: str) -> str:
        m = _FILENAME_RE.match(Path(file_path).name)
        if not m:
            raise ValueError(
                f"Cannot extract account ID from filename: {Path(file_path).name}"
            )
        return m.group(1)

    def get_period(self, file_path: str) -> str:
        m = _FILENAME_RE.match(Path(file_path).name)
        if m:
            return m.group(2)
        with pdfplumber.open(file_path) as pdf:
            text = pdf.pages[0].extract_text() or ""
        m = _PERIOD_TEXT_RE.search(text)
        if not m:
            raise ValueError("Could not find statement period in WS PDF")
        return m.group(1)[:7]

    def parse(self, file_path: str) -> list[dict]:
        transactions = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                cols = self._find_columns(page)
                if not cols:
                    continue
                transactions.extend(self._parse_page(page, cols))
        return self._attach_source(transactions, file_path, self.get_period(file_path))

    @staticmethod
    def _find_columns(page):
        """Find column x-boundaries from the Activity section header row."""
        words = page.extract_words()
        activity_top = None
        for w in words:
            if w["text"] == "Activity":
                activity_top = w["top"]
                break
        if activity_top is None:
            return None

        cols = {}
        for w in words:
            if not (activity_top < w["top"] < activity_top + 25):
                continue
            text = w["text"]
            if text == "Date":
                cols["date_x0"] = w["x0"]
                cols["header_top"] = w["top"]
            elif text == "Transaction":
                cols["txn_x0"] = w["x0"]
            elif text == "Description":
                cols["desc_x0"] = w["x0"]
            elif text in ("Charged", "Debit"):
                cols["charged_x0"] = w["x0"]
            elif text == "Credit" and w["x0"] > 300:
                cols["credit_x0"] = w["x0"]
            elif text == "Balance" and w["x0"] > 400:
                cols["balance_x0"] = w["x0"]

        required = {"header_top", "txn_x0", "desc_x0",
                     "charged_x0", "credit_x0", "balance_x0"}
        if not required.issubset(cols):
            return None
        return cols

    def _parse_page(self, page, cols):
        words = page.extract_words()
        lines = self._group_by_line(words, cols["header_top"])

        transactions = []
        for line in lines:
            date_str = None
            code = None
            desc_words = []
            charged = None
            credit = None

            for w in line:
                x0 = w["x0"]
                text = w["text"]

                if x0 < cols["charged_x0"] - 10:
                    # Left area: Date / Transaction / Description
                    if _DATE_RE.match(text):
                        date_str = text
                    elif (x0 >= cols["txn_x0"] - 5
                          and x0 < cols["desc_x0"] - 5
                          and text.isupper() and text.isalpha()):
                        code = text
                    elif x0 >= cols["desc_x0"] - 5:
                        desc_words.append(text)
                elif x0 < cols["credit_x0"] - 10:
                    if _AMOUNT_RE.match(text):
                        charged = text
                elif x0 < cols["balance_x0"] - 10:
                    if _AMOUNT_RE.match(text):
                        credit = text
                # else: Balance column — skip

            if not date_str or not code or not (charged or credit):
                continue

            if charged and credit:
                amount = self._parse_amount(credit) - self._parse_amount(charged)
            elif credit:
                amount = self._parse_amount(credit)
            else:
                amount = -self._parse_amount(charged)

            description = " ".join(desc_words)
            merchant, note = self._split_description(description)

            transactions.append({
                "transactionDate": datetime.strptime(date_str, "%Y-%m-%d"),
                "merchant": merchant,
                "amount": amount,
                "account": self.ACCOUNT,
                "type": code.lower(),
                "note": note,
            })

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
    def _parse_amount(s: str) -> float:
        return float(s.lstrip("$").replace(",", ""))

    @staticmethod
    def _split_description(desc: str) -> tuple[str, str]:
        """Split 'TICKER - Name: details' into (ticker, details)."""
        parts = desc.split(": ", 1)
        if len(parts) == 2:
            return parts[0].strip(), parts[1].strip()
        return desc.strip(), ""
