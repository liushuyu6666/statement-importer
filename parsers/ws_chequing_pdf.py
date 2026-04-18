"""Parser for WealthSimple Chequing PDF monthly statements.

Handles WealthSimple chequing accounts issued by Wealthsimple Payments Inc.

Filename pattern:
    {AccountID}_{IdentityID}_{YYYY-MM}_v_{version}.pdf
    e.g. WK3K7WN36CAD_identity-ksoVMzmpPcCBcK0OPX9Icg5ZPRL_2025-08_v_0.pdf

PDF columns (Activity section): DATE, POSTED DATE, DESCRIPTION,
    AMOUNT (CAD), BALANCE (CAD)

Amounts are in a single column: positive for deposits, en-dash prefixed
for withdrawals (e.g. $1,000.00 vs \u2013$13.07).

Parser hierarchy:

    StatementParser (ABC)                   \u2014 base.py
    \u251c\u2500\u2500 BMOChequingParser                   \u2014 bmo_chequing.py
    \u251c\u2500\u2500 RBCMasterCardParser                 \u2014 rbc_mastercard.py
    \u251c\u2500\u2500 RBCPersonalParser                   \u2014 rbc_personal.py
    \u2502   \u251c\u2500\u2500 RBCChequingParser               \u2014 rbc_chequing.py
    \u2502   \u2514\u2500\u2500 RBCSavingsParser                \u2014 rbc_savings.py
    \u251c\u2500\u2500 RBCInvestmentParser                 \u2014 rbc_investment.py
    \u2502   \u251c\u2500\u2500 RBCTFSAParser                   \u2014 rbc_tfsa.py
    \u2502   \u2514\u2500\u2500 RBCRRSPParser                   \u2014 rbc_rrsp.py
    \u251c\u2500\u2500 WealthSimpleParser                  \u2014 ws.py
    \u251c\u2500\u2500 WealthSimplePDFParser               \u2014 ws_pdf.py
    \u2514\u2500\u2500 WealthSimpleChequingPDFParser       \u2014 ws_chequing_pdf.py (this file)
"""

import re
from datetime import datetime
from pathlib import Path

import pdfplumber

from .base import StatementParser
from .ws_common import ws_account_name

# e.g. "WK3K7WN36CAD_identity-ksoVMzmpPcCBcK0OPX9Icg5ZPRL_2025-08_v_0.pdf"
_FILENAME_RE = re.compile(r"^([A-Z0-9]+)_.+_(\d{4}-\d{2})_v_\d+\.pdf$")

# Period in PDF text: "Aug 1 - Aug 31, 2025"
_PERIOD_TEXT_RE = re.compile(
    r"([A-Z][a-z]{2})\s+\d{1,2}\s*-\s*[A-Z][a-z]{2}\s+\d{1,2},\s*(\d{4})"
)

# "Account number: 15100555"
_ACCOUNT_NO_RE = re.compile(r"Account\s+number:\s*(\S+)")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_AMOUNT_RE = re.compile(r"^[\u2013\-]?\$[\d,]+\.\d{2}$")


class WealthSimpleChequingPDFParser(StatementParser):

    def __init__(self, account_id: str, pdf_account_no: str):
        self.ACCOUNT = ws_account_name(account_id)
        self.pdf_account_no = pdf_account_no

    @staticmethod
    def matches(first_page_text: str) -> bool:
        text = first_page_text.replace(" ", "").lower()
        return ("chequingmonthlystatement" in text
                or "cashmonthlystatement" in text)

    @staticmethod
    def validate(full_text: str, cardholder_name: str) -> list[str]:
        errors = []
        if "Activity" not in full_text:
            errors.append("Missing 'Activity' section")
        return errors

    @staticmethod
    def extract_account_id(file_path: str) -> str:
        m = _FILENAME_RE.match(Path(file_path).name)
        if not m:
            raise ValueError(
                f"Cannot extract account ID from filename: {Path(file_path).name}"
            )
        return m.group(1)

    @staticmethod
    def extract_pdf_account_no(first_page_text: str) -> str:
        m = _ACCOUNT_NO_RE.search(first_page_text)
        return m.group(1) if m else ""

    def get_period(self, file_path: str) -> str:
        m = _FILENAME_RE.match(Path(file_path).name)
        if m:
            return m.group(2)
        with pdfplumber.open(file_path) as pdf:
            text = pdf.pages[0].extract_text() or ""
        m = _PERIOD_TEXT_RE.search(text)
        if not m:
            raise ValueError("Could not find statement period in WS Chequing PDF")
        month_num = datetime.strptime(m.group(1), "%b").month
        return f"{m.group(2)}-{month_num:02d}"

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
        words = page.extract_words()
        header_top = None
        for w in words:
            if w["text"] == "DATE" and header_top is None:
                header_top = w["top"]
                break
        if header_top is None:
            return None

        cols = {"header_top": header_top}
        for w in words:
            if not (header_top - 2 <= w["top"] <= header_top + 2):
                continue
            text = w["text"]
            if text == "DESCRIPTION":
                cols["desc_x0"] = w["x0"]
            elif text == "AMOUNT":
                cols["amount_x0"] = w["x0"]
            elif text == "BALANCE":
                cols["balance_x0"] = w["x0"]

        required = {"desc_x0", "amount_x0", "balance_x0"}
        if not required.issubset(cols):
            return None
        return cols

    def _parse_page(self, page, cols):
        words = page.extract_words()
        lines = self._group_by_line(words, cols["header_top"])

        transactions = []
        for line in lines:
            date_str = None
            desc_words = []
            amount_str = None

            for w in line:
                x0 = w["x0"]
                text = w["text"]

                if x0 < cols["desc_x0"] - 10:
                    if _DATE_RE.match(text) and date_str is None:
                        date_str = text
                elif x0 < cols["amount_x0"] - 10:
                    desc_words.append(text)
                elif x0 < cols["balance_x0"] - 10:
                    if _AMOUNT_RE.match(text):
                        amount_str = text

            if not date_str or not amount_str:
                continue

            amount = self._parse_amount(amount_str)
            transactions.append({
                "transactionDate": datetime.strptime(date_str, "%Y-%m-%d"),
                "merchant": " ".join(desc_words),
                "amount": amount,
                "account": self.ACCOUNT,
                "type": "deposit" if amount > 0 else "withdrawal",
                "note": self.pdf_account_no,
            })

        return transactions

    @staticmethod
    def _group_by_line(words, header_top, tolerance=2):
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
        if s.startswith("\u2013") or s.startswith("-"):
            return -float(s[1:].lstrip("$").replace(",", ""))
        return float(s.lstrip("$").replace(",", ""))
