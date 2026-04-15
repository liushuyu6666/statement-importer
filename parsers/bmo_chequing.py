"""Parser for BMO Everyday Banking chequing account statements.

Uses word-level extraction (x_tolerance=1 to prevent word merging) with
x-position column classification, similar to RBC personal statements but
adapted for BMO's layout.

Column headers: Date, Description, Amounts deducted from your account ($),
Amounts added to your account ($), Balance ($).

Parser hierarchy:

    StatementParser (ABC)                   — base.py
    ├── BMOChequingParser                   — bmo_chequing.py (this file)
    ├── RBCMasterCardParser                 — rbc_mastercard.py
    ├── RBCPersonalParser                   — rbc_personal.py
    │   ├── RBCChequingParser               — rbc_chequing.py
    │   └── RBCSavingsParser                — rbc_savings.py
    └── RBCInvestmentParser                 — rbc_investment.py
        ├── RBCTFSAParser                   — rbc_tfsa.py
        └── RBCRRSPParser                   — rbc_rrsp.py
"""

import re
from datetime import datetime

import pdfplumber

from .base import StatementParser

# "For the period ending March 09, 2026" — handles optional missing spaces
_PERIOD_END_RE = re.compile(
    r"For\s*the\s*period\s*ending\s*([A-Za-z]+)\s*(\d{1,2})\s*,\s*(\d{4})"
)

_REQUIRED_FEATURES = [
    ("BMO", "Missing BMO branding"),
    ("Everyday Banking", "Missing 'Everyday Banking' header"),
    ("Here's what happened in your account", "Missing account activity section"),
    ("Amounts deducted", "Missing deducted column"),
    ("Amounts added", "Missing added column"),
]

_AMOUNT_RE = re.compile(r"^[\d,]+\.\d{2}$")
_MONTH_RE = re.compile(r"^[A-Z][a-z]{2}$")
_DAY_RE = re.compile(r"^\d{1,2}$")

_SKIP_DESCRIPTIONS = {"Openingbalance", "Closingtotals"}


class BMOChequingParser(StatementParser):
    ACCOUNT = "BMO Chequing"

    @staticmethod
    def matches(first_page_text: str) -> bool:
        text = first_page_text.replace(" ", "").lower()
        return "bmo" in text and "everydaybanking" in text and "chequingaccount" in text

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
            end_date = self._extract_end_date(pdf)
        return f"ending {end_date.strftime('%Y-%m-%d')}"

    def parse(self, pdf_path: str) -> list[dict]:
        transactions = []
        with pdfplumber.open(pdf_path) as pdf:
            end_date = self._extract_end_date(pdf)
            for page in pdf.pages:
                col_bounds = self._find_columns(page)
                if not col_bounds:
                    continue
                transactions.extend(
                    self._parse_page(page, col_bounds, end_date)
                )
        return transactions

    @staticmethod
    def _extract_end_date(pdf) -> datetime:
        text = pdf.pages[0].extract_text() or ""
        m = _PERIOD_END_RE.search(text)
        if m:
            return datetime.strptime(
                f"{m.group(1)} {m.group(2)}, {m.group(3)}", "%B %d, %Y"
            )
        raise ValueError("Could not find statement period in PDF")

    @staticmethod
    def _find_columns(page):
        """Find x-boundaries for each column from the header row.

        Locates the words "deducted" and "added" (unique to column headers)
        to establish column centers, then uses midpoints to classify amounts.
        Uses x_tolerance=1 to prevent pdfplumber from merging adjacent words.
        """
        words = page.extract_words(x_tolerance=1)
        cols = {}
        deducted_top = None

        for w in words:
            text = w["text"]
            if text == "deducted":
                cols["deducted_x0"] = w["x0"]
                cols["deducted_x1"] = w["x1"]
                deducted_top = w["top"]
            elif text == "added" and deducted_top is not None:
                if abs(w["top"] - deducted_top) < 5 and w["x0"] > cols.get("deducted_x0", 0):
                    cols["added_x0"] = w["x0"]
                    cols["added_x1"] = w["x1"]
            elif text == "Balance" and "added_x0" in cols and w["x0"] > cols["added_x0"]:
                cols["balance_x0"] = w["x0"]
            elif text == "Date" and "header_top" not in cols:
                cols["date_x0"] = w["x0"]
                cols["header_top"] = w["top"]

        if "deducted_x0" not in cols or "added_x0" not in cols:
            return None

        ded_center = (cols["deducted_x0"] + cols["deducted_x1"]) / 2
        add_center = (cols["added_x0"] + cols["added_x1"]) / 2
        cols["ded_add_mid"] = (ded_center + add_center) / 2
        if "balance_x0" in cols:
            cols["add_bal_mid"] = (add_center + cols["balance_x0"]) / 2
        else:
            cols["add_bal_mid"] = add_center + 100
        return cols

    def _parse_page(self, page, cols, end_date):
        words = page.extract_words(x_tolerance=1)
        lines = self._group_by_line(words, cols["header_top"])

        transactions = []
        current_date_str = None
        description_parts = []
        pending_amount = None

        for line in lines:
            left_words = []
            deducted = None
            added = None

            for w in line:
                x0 = w["x0"]
                text = w["text"]

                if "date_x0" in cols and x0 < cols["date_x0"] - 5:
                    continue

                if x0 < cols["deducted_x0"] - 10:
                    left_words.append(text)
                elif x0 < cols["ded_add_mid"] and _AMOUNT_RE.match(text):
                    deducted = text
                elif x0 < cols["add_bal_mid"] and _AMOUNT_RE.match(text):
                    added = text
                # else: balance column — skip

            # Check if left_words starts with a date (month abbreviation + day)
            date_str = None
            if (len(left_words) >= 2
                    and _MONTH_RE.match(left_words[0])
                    and _DAY_RE.match(left_words[1])):
                date_str = f"{left_words[0]} {left_words[1]}"
                desc_words = left_words[2:]
            else:
                desc_words = left_words

            if date_str:
                # Finalize any pending transaction before starting a new one
                if pending_amount is not None and current_date_str and description_parts:
                    transactions.append({
                        "transactionDate": self._parse_date(
                            current_date_str, end_date
                        ),
                        "merchant": " ".join(description_parts),
                        "amount": pending_amount,
                        "account": self.ACCOUNT,
                        "note": "",
                    })
                description_parts = []
                current_date_str = date_str
                pending_amount = None

            desc = " ".join(desc_words)

            # Skip page footer "Page N of N"
            if (len(desc_words) >= 4 and desc_words[0] == "Page"
                    and desc_words[2] == "of"
                    and desc_words[1].isdigit() and desc_words[3].isdigit()):
                continue

            if desc and desc.replace(" ", "") not in _SKIP_DESCRIPTIONS:
                if current_date_str:
                    description_parts.append(desc)

            # Only record amount if there's actual description to go with it
            # (avoids "Closing totals" amounts creating bogus transactions)
            if (deducted or added) and description_parts:
                pending_amount = (
                    -self._parse_amount(deducted)
                    if deducted
                    else self._parse_amount(added)
                )

        # Finalize last pending transaction on the page
        if pending_amount is not None and current_date_str and description_parts:
            transactions.append({
                "transactionDate": self._parse_date(
                    current_date_str, end_date
                ),
                "merchant": " ".join(description_parts),
                "amount": pending_amount,
                "account": self.ACCOUNT,
                "note": "",
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
    def _parse_date(date_str, end_date):
        """Parse dates like 'Feb 10' using the statement end date to determine year.

        Parses with year included to handle leap day (Feb 29) correctly.
        """
        for year in (end_date.year, end_date.year - 1):
            try:
                candidate = datetime.strptime(f"{date_str} {year}", "%b %d %Y")
            except ValueError:
                continue
            if candidate <= end_date:
                return candidate
        return datetime.strptime(f"{date_str} {end_date.year}", "%b %d %Y")

    @staticmethod
    def _parse_amount(s):
        return float(s.replace(",", ""))
