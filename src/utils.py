import os

from dotenv import load_dotenv

load_dotenv()


def ssl_verify() -> bool:
    """Return False when HTTPX_VERIFY_SSL=false, True otherwise (safe default)."""
    return os.getenv("HTTPX_VERIFY_SSL", "true").strip().lower() != "false"
