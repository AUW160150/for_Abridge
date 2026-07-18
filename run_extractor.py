"""Phase 1: stream the scripted transcript through the Claude extractor.

Usage:
    python run_extractor.py [path/to/transcript.txt]

Prints each ClinicalEvent to the console as it is extracted, with the source
utterance and confidence attached. Requires ANTHROPIC_API_KEY (or an
`ant auth login` profile).
"""

import os
import sys
from pathlib import Path

import anthropic

from codeclock.display import print_event
from codeclock.extractor import Extractor

TRANSCRIPT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/transcript.txt")

NO_CREDENTIALS_MSG = (
    "No Anthropic credentials found.\n"
    "Set one with:  export ANTHROPIC_API_KEY=sk-ant-...\n"
    "or log in via: ant auth login"
)


def main() -> None:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        # The SDK can also resolve an `ant auth login` profile; only bail if
        # constructing a client would fail outright.
        try:
            anthropic.Anthropic()._validate_headers({}, {})  # cheap credential probe
        except Exception:
            sys.exit(NO_CREDENTIALS_MSG)

    text = TRANSCRIPT.read_text()
    extractor = Extractor()
    print(f"=== Code Clock extractor — streaming {TRANSCRIPT} ===\n")
    try:
        for event in extractor.stream(text):
            print_event(event, extractor.code_start)
            print()
    except anthropic.AuthenticationError:
        sys.exit(NO_CREDENTIALS_MSG)
    print(f"=== done — {len(extractor.events)} events extracted ===")


if __name__ == "__main__":
    main()
