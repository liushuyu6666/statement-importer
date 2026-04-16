import os


def ws_account_name(account_no: str) -> str:
    """Build a WealthSimple account name from the account number.

    Looks up ``WS_ACCOUNT_<account_no>`` in the environment for a
    human-readable source label (e.g. "Cash", "TFSA").  Returns
    ``WS <Source>-<Account>`` when a mapping exists, otherwise
    ``WS <Account>``.
    """
    source = os.environ.get(f"WS_ACCOUNT_{account_no}")
    if source:
        return f"WS {source}-{account_no}"
    return f"WS {account_no}"
