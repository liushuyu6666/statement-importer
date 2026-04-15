from abc import ABC, abstractmethod


class StatementParser(ABC):
    """Base class for bank statement parsers."""

    ACCOUNT: str  # e.g. "RBC MasterCard"

    @staticmethod
    @abstractmethod
    def matches(first_page_text: str) -> bool:
        """Return True if this parser can handle the given statement."""

    @staticmethod
    @abstractmethod
    def validate(full_text: str, cardholder_name: str) -> list[str]:
        """Check that the PDF has the expected structure for this format.

        Returns a list of error messages. Empty list means valid.
        """

    @abstractmethod
    def get_period(self, pdf_path: str) -> str:
        """Return a string identifying the statement period.

        Used for deduplication — if (account, period) already exists in
        file_status with status "done", the file is skipped.
        """

    @abstractmethod
    def parse(self, pdf_path: str) -> list[dict]:
        """Parse the PDF and return a list of transaction dicts.

        Each dict must have:
            transactionDate: datetime
            merchant: str
            amount: float
            account: str
        """
