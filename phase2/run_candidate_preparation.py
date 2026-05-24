#!/usr/bin/env python3
"""Compatibility wrapper for the TED candidate preparation entry."""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from phase2.ted.run_candidate_preparation import main


if __name__ == "__main__":
    main()
