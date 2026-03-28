#!/usr/bin/env python3
"""One-time OAuth setup script.

Run this once on initial setup (or after refresh token expiry) to authenticate
with your Microsoft personal account and cache the OAuth tokens.

Usage:
    python run_auth.py
"""

from __future__ import annotations

import logging
import sys

from auth.oauth_flow import run_oauth_flow
from config import Settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    settings = Settings()

    if not settings.azure_client_id or not settings.azure_client_secret:
        print("ERROR: AZURE_CLIENT_ID and AZURE_CLIENT_SECRET must be set in .env", file=sys.stderr)
        sys.exit(1)

    if not settings.token_encryption_key:
        print("ERROR: TOKEN_ENCRYPTION_KEY must be set in .env", file=sys.stderr)
        print(
            'Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
        sys.exit(1)

    try:
        run_oauth_flow(settings)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
