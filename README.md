# Bank Statement Importer

Extracts transactions from bank statement PDFs and CSVs and imports them into MongoDB.

Supports multiple bank/card formats via auto-detection — just drop your statements into `statements/` and run.

## Project Structure

```
statement-importer/
├── main.py                # Entry point — scans files, detects format, imports to MongoDB
├── parsers/
│   ├── __init__.py        # Auto-detection registry
│   ├── base.py            # StatementParser ABC
│   ├── bmo_chequing.py    # BMO Chequing account
│   ├── rbc_mastercard.py  # RBC MasterCard credit card
│   ├── rbc_personal.py    # Shared base for RBC personal account statements
│   ├── rbc_chequing.py    # RBC Day to Day Banking chequing account
│   ├── rbc_savings.py     # RBC High Interest eSavings account
│   ├── rbc_investment.py  # Shared base for RBC investment account statements
│   ├── rbc_tfsa.py        # RBC TFSA
│   ├── rbc_rrsp.py        # RBC RRSP
│   ├── ws_common.py       # Shared WealthSimple account name helper
│   ├── ws.py              # WealthSimple CSV statements
│   └── ws_pdf.py          # WealthSimple PDF statements (ShareOwner format)
├── statements/            # Drop statement PDFs/CSVs here
├── requirements.txt
├── .example.env
├── .env                   # Configuration (not tracked)
└── .venv/
```

### Parser hierarchy

```
StatementParser (ABC)
├── BMOChequingParser            # Word-position parsing with Withdrawals/Deposits columns
├── RBCMasterCardParser          # Unique format: single Amount column, regex text parsing
├── RBCPersonalParser            # Shared format: Withdrawals/Deposits columns, word-position parsing
│   ├── RBCChequingParser
│   └── RBCSavingsParser
├── RBCInvestmentParser          # Shared format: fund-level transaction activity, regex text parsing
│   ├── RBCTFSAParser
│   └── RBCRRSPParser
├── WealthSimpleParser           # CSV: header-based detection, all WS account types
└── WealthSimplePDFParser        # PDF: ShareOwner format, word-position column parsing
```

RBC MasterCard has a unique PDF layout and extends `StatementParser` directly.
RBC chequing and savings share the same layout via `RBCPersonalParser`. RBC TFSA
and RRSP share a different investment statement layout via `RBCInvestmentParser`.
WealthSimple provides both CSV and PDF statements — the CSV parser detects by
header row and the PDF parser detects by "ORDER EXECUTION ONLY ACCOUNT" header.
Both extract the account number from the file and look up a human-readable
source label via `WS_ACCOUNT_<AccountNo>` env vars (see `.example.env`),
producing names like `WS Cash-XXXXXXXXXXXX`. Unmapped accounts fall back
to `WS <AccountNo>`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy the example env file and fill in your name:

```bash
cp .example.env .env
```

Then edit `.env` with your name and WealthSimple account mappings:

```
CARDHOLDER_NAME=YOUR NAME

# WealthSimple account mappings (optional)
WS_ACCOUNT_XXXXXXXXXXXX=Crypto
WS_ACCOUNT_YYYYYYYYYYYY=Cash
```

Requires MongoDB running locally on the default port (27017).

## Usage

```bash
# Import all statements in the folder
python main.py statements/

# Import a single file
python main.py statements/some-statement.pdf
python main.py statements/some-statement.csv
```

Duplicate statements are skipped automatically via statement-level dedup on `(account, period)` in the `file_status` collection, so it's safe to re-run or import overlapping statements. Empty files (0 bytes) are skipped with status `"skipped"`.

## MongoDB Schema

Database: `personal_finance`

### `transactions` collection

| Field             | Type     | Example                              |
|-------------------|----------|--------------------------------------|
| `transactionDate` | Date     | `2026-02-10T00:00:00.000Z`          |
| `merchant`        | String   | `LOBLAW TORONTO EGLINTO TORONTO ON`  |
| `amount`          | Number   | `32.75` (negative for payments)      |
| `account`         | String   | `RBC MasterCard`, `BMO Chequing`, `WS Cash-XXXXXXXXXXXX` |
| `type`            | String   | `purchase`, `deposit`, `buy`, `cont` |
| `note`            | String   | Additional details (may be empty)    |
| `createdAt`       | Date     | `2026-04-14T21:50:34.506Z`          |

#### `type` values by parser

| Parser                        | Possible values                                                   |
|-------------------------------|-------------------------------------------------------------------|
| RBC MasterCard                | `purchase`, `payment`                                             |
| RBC Chequing / Savings        | `withdrawal`, `deposit`                                           |
| BMO Chequing                  | `withdrawal`, `deposit`                                           |
| RBC TFSA / RRSP               | `contribution`, `investment switch`, `income reinvested`, `return of capital` |
| WealthSimple (CSV and PDF)    | Transaction codes lowercased: `buy`, `sell`, `cont`, `wd`, `div`, `dep`, `fee`, `int`, `trfin`, `trfout`, etc. (full list on page 3 of each WS PDF) |

### `file_status` collection

Tracks processing status per statement for dedup. Unique index on `(account, period)`.

| Field         | Type     | Example                              |
|---------------|----------|--------------------------------------|
| `fileName`    | String   | `eStatement_2026-03-10.pdf`          |
| `account`     | String   | `RBC MasterCard`                     |
| `period`      | String   | `2025-12-11 to 2026-01-10`          |
| `status`      | String   | `done`, `failed`, or `skipped`       |
| `processedAt` | Date     | `2026-04-14T21:50:34.506Z`          |
| `error`       | String   | Error message (only when failed/skipped) |

## Validation

Each file is validated before parsing:

- Format detection (which bank/card type, by PDF content or CSV headers)
- Structural checks (expected headers, table columns, balance sections)
- Cardholder name verification against `CARDHOLDER_NAME` in `.env` (PDF only)
- Empty files (0 bytes) are skipped automatically

Unrecognized or invalid files are skipped with a message.

## Adding a New Parser

For a completely new PDF statement format:

1. Create `parsers/your_bank.py` extending `StatementParser`
2. Implement `matches()`, `validate()`, `get_period()`, and `parse()`
3. Register it in `parsers/__init__.py`

For another RBC personal account type (same PDF layout as chequing/savings):

1. Create `parsers/rbc_your_account.py` extending `RBCPersonalParser`
2. Define `ACCOUNT`, `_MATCH_KEYWORD`, and `_ACCOUNT_TYPE_FEATURE`
3. Register it in `parsers/__init__.py`

For another RBC investment account type (same PDF layout as TFSA/RRSP):

1. Create `parsers/rbc_your_account.py` extending `RBCInvestmentParser`
2. Define `ACCOUNT`, `_MATCH_KEYWORD`, and `_ACCOUNT_TYPE_FEATURE`
3. Register it in `parsers/__init__.py`

For a new CSV format:

1. Create `parsers/your_bank.py` extending `StatementParser`
2. Implement `matches()` to detect by CSV header row
3. Add detection in `_detect_csv_parser()` in `parsers/__init__.py`
