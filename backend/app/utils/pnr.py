"""Passenger Name Record (PNR) generation utilities."""

import secrets
import string

# Characters allowed in PNR codes: excludes ambiguous characters 0, O, 1, I
PNR_ALPHABET = "".join(
    char
    for char in (string.ascii_uppercase + string.digits)
    if char not in {"0", "O", "1", "I"}
)


def generate_pnr(length: int = 6) -> str:
    """Generate a random uppercase alphanumeric PNR code.

    Args:
        length: Number of characters for the PNR code (default: 6).

    Returns:
        A randomly generated PNR string without ambiguous characters.
    """
    return "".join(secrets.choice(PNR_ALPHABET) for _ in range(length))


def is_valid_pnr(pnr: str, expected_length: int = 6) -> bool:
    """Validate whether a given PNR meets formatting constraints."""
    if not isinstance(pnr, str):
        return False
    if len(pnr) != expected_length:
        return False
    return all(char in PNR_ALPHABET for char in pnr)
