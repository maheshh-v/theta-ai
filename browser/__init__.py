"""
Theta's hands: a real Chromium browser the agent can operate.

    from browser.session import BrowserSession
    from browser.snapshot import Snapshot, Element
    from browser.actions import Actions
    from browser.guard import classify, scan_for_injection

The design choice that matters is in `snapshot.py`: the agent never sees pixels
and never guesses coordinates. Each observation is a numbered list of the page's
interactive elements, and every action names one by index. That makes actions
deterministic, cheap, and replayable — which is what `playbooks` later depends on.
"""
