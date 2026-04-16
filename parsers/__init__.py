from .bmo_chequing import BMOChequingParser
from .rbc_chequing import RBCChequingParser
from .rbc_mastercard import RBCMasterCardParser
from .rbc_rrsp import RBCRRSPParser
from .rbc_savings import RBCSavingsParser
from .rbc_tfsa import RBCTFSAParser
from .ws import WealthSimpleParser
from .ws_pdf import WealthSimplePDFParser

PDF_PARSERS = [
    BMOChequingParser,
    RBCMasterCardParser,
    RBCChequingParser,
    RBCSavingsParser,
    RBCTFSAParser,
    RBCRRSPParser,
]


def detect_parser(file_path: str, cardholder_name: str):
    """Detect the statement format, validate structure, and return a parser.

    Routes PDF files through pdfplumber-based detection and CSV files
    through header-based detection.
    """
    if file_path.lower().endswith(".csv"):
        return _detect_csv_parser(file_path)
    return _detect_pdf_parser(file_path, cardholder_name)


def _detect_pdf_parser(pdf_path: str, cardholder_name: str):
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        first_page_text = pdf.pages[0].extract_text() or ""
        full_text = "\n".join(
            page.extract_text() or "" for page in pdf.pages
        )

    for parser_cls in PDF_PARSERS:
        if parser_cls.matches(first_page_text):
            errors = parser_cls.validate(full_text, cardholder_name)
            if errors:
                raise ValueError(
                    f"PDF matched {parser_cls.ACCOUNT} but failed validation:\n"
                    + "\n".join(f"  - {e}" for e in errors)
                )
            return parser_cls()

    # WealthSimple PDF needs dynamic account type (like the CSV parser)
    if WealthSimplePDFParser.matches(first_page_text):
        errors = WealthSimplePDFParser.validate(full_text, cardholder_name)
        if errors:
            raise ValueError(
                f"PDF matched WealthSimple but failed validation:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )
        account_type = WealthSimplePDFParser.extract_account_type(first_page_text)
        return WealthSimplePDFParser(account_type)

    raise ValueError(
        f"No parser recognized this statement. "
        f"Supported formats: {', '.join(p.ACCOUNT for p in PDF_PARSERS)}"
    )


def _detect_csv_parser(file_path: str):
    with open(file_path) as f:
        first_line = f.readline()

    if WealthSimpleParser.matches(first_line):
        account_type = WealthSimpleParser.extract_account_type(file_path)
        return WealthSimpleParser(account_type)

    raise ValueError("No CSV parser recognized this statement.")
