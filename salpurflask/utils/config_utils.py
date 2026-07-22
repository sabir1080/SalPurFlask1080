"""Configuration and environment utilities."""

import os


def is_demo_mode():
    """Check if the application is running in demo mode.

    Returns:
        True if DEMO_MODE environment variable is set to true/yes/1
    """
    return os.getenv("DEMO_MODE", "false").lower() in ("1", "true", "yes")


__all__ = ['is_demo_mode']
