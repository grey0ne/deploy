"""Errors `dc` reports as a message, without a traceback (importable before the environment)."""


class EnvConfigError(Exception):
    """Required environment variables are missing or empty."""
