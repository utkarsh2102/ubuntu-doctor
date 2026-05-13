"""ANSI color helpers for the text renderer.

Raw escape codes, no external dependency. Auto-disabled when stdout
isn't a TTY, when ``NO_COLOR`` is set (per https://no-color.org/), or
when the caller explicitly opts out.
"""

from __future__ import annotations

import os
import sys

_RESET = "\033[0m"
_CODES = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}


def supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


class Palette:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, text: str, *codes: str) -> str:
        if not self.enabled or not text:
            return text
        prefix = "".join(_CODES[c] for c in codes)
        return f"{prefix}{text}{_RESET}"

    def bold(self, text: str) -> str:
        return self._wrap(text, "bold")

    def dim(self, text: str) -> str:
        return self._wrap(text, "dim")

    def red(self, text: str) -> str:
        return self._wrap(text, "bold", "red")

    def green(self, text: str) -> str:
        return self._wrap(text, "green")

    def yellow(self, text: str) -> str:
        return self._wrap(text, "yellow")

    def cyan(self, text: str) -> str:
        return self._wrap(text, "cyan")
