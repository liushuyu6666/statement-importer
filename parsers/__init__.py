from .rbc_chequing import RBCChequingParser
from .rbc_mastercard import RBCMasterCardParser
from .rbc_savings import RBCSavingsParser

PARSERS = [
    RBCMasterCardParser,
    RBCChequingParser,
    RBCSavingsParser,
]


def detect_parser(pdf_path: str, cardholder_name: str):
    """Detect the statement format, validate structure, and return a parser.

    Reads the PDF once, matches against known formats, then validates
    that the PDF has the expected structural features before returning.
    """
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        first_page_text = pdf.pages[0].extract_text() or ""
        full_text = "\n".join(
            page.extract_text() or "" for page in pdf.pages
        )

    for parser_cls in PARSERS:
        if parser_cls.matches(first_page_text):
            errors = parser_cls.validate(full_text, cardholder_name)
            if errors:
                raise ValueError(
                    f"PDF matched {parser_cls.ACCOUNT} but failed validation:\n"
                    + "\n".join(f"  - {e}" for e in errors)
                )
            return parser_cls()

    raise ValueError(
        f"No parser recognized this statement. "
        f"Supported formats: {', '.join(p.ACCOUNT for p in PARSERS)}"
    )
