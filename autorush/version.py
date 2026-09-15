"""Version et identite de l'application."""

APP_NAME = "AutoRush"
__version__ = "1.0.0"
BUILD_CHANNEL = "stable"


def version_string() -> str:
    return f"{APP_NAME} {__version__}"
