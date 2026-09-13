"""
telegram_service/local_test.py — try the message pipeline (to-do commands
+ general Q&A) from a terminal, with no bot token, no deployment, and no
network exposure at all.

Usage:
    python -m telegram_service.local_test
    python -m telegram_service.local_test "add buy milk"
"""
from __future__ import annotations

import sys

from telegram_service import handler


def run_single(text: str) -> None:
    print(f"You:    {text}")
    print(f"JARVIS: {handler.generate_reply(text)}")


def run_interactive() -> None:
    print("(type 'quit' or Ctrl+C to exit)\n")
    try:
        while True:
            text = input("You:    ").strip()
            if not text:
                continue
            if text.lower() in ("quit", "exit"):
                break
            print(f"JARVIS: {handler.generate_reply(text)}")
    except (KeyboardInterrupt, EOFError):
        print()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_single(" ".join(sys.argv[1:]))
    else:
        run_interactive()
