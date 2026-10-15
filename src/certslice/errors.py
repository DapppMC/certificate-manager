class InputError(Exception):
    """Bad input found before anything is written (exit code 2)."""


class AccountingError(Exception):
    """Pages lost or duplicated (exit code 3)."""
