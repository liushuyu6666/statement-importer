from .rbc_personal import RBCPersonalParser


class RBCChequingParser(RBCPersonalParser):
    ACCOUNT = "RBC Chequing"
    _MATCH_KEYWORD = "personalbanking"
    _ACCOUNT_TYPE_FEATURE = ("personal banking", "Missing 'personal banking' header")
