from .rbc_personal import RBCPersonalParser


class RBCSavingsParser(RBCPersonalParser):
    ACCOUNT = "RBC Savings"
    _MATCH_KEYWORD = "personalsavings"
    _ACCOUNT_TYPE_FEATURE = ("personal savings", "Missing 'personal savings' header")
