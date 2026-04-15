from .rbc_investment import RBCInvestmentParser


class RBCTFSAParser(RBCInvestmentParser):
    ACCOUNT = "RBC TFSA"
    _MATCH_KEYWORD = "tax-freesavingsaccount"
    _ACCOUNT_TYPE_FEATURE = ("Tax-Free Savings Account", "Missing TFSA account type")
