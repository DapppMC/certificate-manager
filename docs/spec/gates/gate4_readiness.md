# Gate 4: Definition of Ready
Deliver only when every box is honestly checked.

- [ ] Brief approved (Gate 0) and anomalies decided.
- [ ] Requirements approved, each with acceptance (Gate 1).
- [ ] Design covers every requirement (Gate 2).
- [ ] Traceability matrix complete, protected tests listed (Gate 3).
- [ ] Every ticket passes `python tools/ticketc.py validate <ticket>`.
- [ ] Tickets have allowed_paths, forbidden_paths, acceptance commands, stop_if.
- [ ] No secrets in any file (scan done).
- [ ] `python -m unittest discover tests` exits 0.
- [ ] `progress.md` current; resume command written.

If any box is unchecked, say what is missing. Never write "ready".
