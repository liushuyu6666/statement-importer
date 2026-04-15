from .rbc_investment import RBCInvestmentParser


class RBCRRSPParser(RBCInvestmentParser):
    ACCOUNT = "RBC RRSP"
    _MATCH_KEYWORD = "registeredretirementsavingsplan"
    _ACCOUNT_TYPE_FEATURE = (
        "Registered Retirement Savings Plan",
        "Missing RRSP account type",
    )
