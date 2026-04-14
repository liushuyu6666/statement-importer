# Bank Statement Importer

Extracts transactions from bank statement PDFs and imports them into MongoDB.

Supports multiple bank/card formats via auto-detection — just drop all your PDFs into `statements/` and run.

## Project Structure

```
statement-importer/
├── main.py                # Entry point — scans PDFs, detects format, imports to MongoDB
├── parsers/
│   ├── __init__.py        # Auto-detection registry
│   ├── base.py            # StatementParser ABC
│   ├── rbc_personal.py    # Shared base for RBC personal account statements
│   ├── rbc_mastercard.py  # RBC MasterCard credit card
│   ├── rbc_chequing.py    # RBC Day to Day Banking chequing account
│   └── rbc_savings.py     # RBC High Interest eSavings account
├── statements/            # Drop statement PDFs here
├── requirements.txt
├── .example.env
├── .env                   # Configuration (not tracked)
└── .venv/
```

### Parser hierarchy

```
StatementParser (ABC)
├── RBCMasterCardParser          # Unique format: single Amount column, regex text parsing
└── RBCPersonalParser            # Shared format: Withdrawals/Deposits columns, word-position parsing
    ├── RBCChequingParser
    └── RBCSavingsParser
```

RBC MasterCard statements have a different PDF layout from the personal account
statements (chequing/savings), so it extends `StatementParser` directly. Chequing
and savings share the same layout and parsing logic via `RBCPersonalParser`.

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

Then edit `.env` with your name:

```
CARDHOLDER_NAME=YOUR NAME
```

Requires MongoDB running locally on the default port (27017).

## Usage

```bash
# Import all statements in the folder
python main.py statements/

# Import a single file
python main.py statements/some-statement.pdf
```

Duplicate transactions are skipped automatically via a unique compound index on `(transactionDate, merchant, amount, account)`, so it's safe to re-run or import overlapping statements.

## MongoDB Schema

Database: `personal_finance`, Collection: `transactions`

| Field             | Type     | Example                              |
|-------------------|----------|--------------------------------------|
| `transactionDate` | Date     | `2026-02-10T00:00:00.000Z`          |
| `merchant`        | String   | `LOBLAW TORONTO EGLINTO TORONTO ON`  |
| `amount`          | Number   | `32.75` (negative for payments)      |
| `account`         | String   | `RBC MasterCard`, `RBC Chequing`, `RBC Savings` |
| `createdAt`       | Date     | `2026-04-14T21:50:34.506Z`          |

## Validation

Each PDF is validated before parsing:

- Format detection (which bank/card type)
- Structural checks (expected headers, table columns, balance sections)
- Cardholder name verification against `CARDHOLDER_NAME` in `.env`

Unrecognized or invalid PDFs are skipped with a message.

## Adding a New Parser

For a completely new statement format:

1. Create `parsers/your_bank.py` extending `StatementParser`
2. Implement `matches()`, `validate()`, and `parse()`
3. Register it in `parsers/__init__.py`

For another RBC personal account type (same PDF layout as chequing/savings):

1. Create `parsers/rbc_your_account.py` extending `RBCPersonalParser`
2. Define `ACCOUNT`, `_MATCH_KEYWORD`, and `_ACCOUNT_TYPE_FEATURE`
3. Register it in `parsers/__init__.py`
