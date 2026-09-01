"""Interface stubs for the two suite actors that land in LATER Ф4 lanes (plan §8).

The skeleton lane declares the CONTRACT surface only — enough for the scenario
waves to be written against a stable name — and refuses loudly instead of
pretending an implementation exists. Instantiating either class is a scenario
bug until its lane lands:

* ``FakeClaudexorDaemon`` — the delegated-transport wave: a loopback claudexord
  imitation serving the protocol-3 handshake, capabilities/quota answers and a
  scripted run lifecycle (started/settled events, receipts), so delegated
  transport + restart recovery + no-orphans scenarios never need a real daemon.
* ``PlaywrightUIClient`` — the gateway/UI-truth wave: a real-browser client over
  the isolated server's web UI (the existing ``tests/test_ui_smoke_playwright.py``
  direct-server idiom, generalized to scenario assertions).
"""

from __future__ import annotations

_NOT_LANDED = (
    "{name} is an interface stub: its implementation lands with the {lane} wave of "
    "the Ф4 integration suite (plan §8). Write the scenario against this surface, "
    "but do not enable it before the lane lands — see tests/system_e2e/ and "
    "docs/v7next/LEDGER_CORRECTIONS.md (F4 lane 1)."
)


class FakeClaudexorDaemon:
    """Loopback claudexord imitation (handshake / caps / quota / scripted runs)."""

    def __init__(self, *_args, **_kwargs) -> None:
        raise NotImplementedError(_NOT_LANDED.format(
            name="FakeClaudexorDaemon", lane="delegated-transport"))

    def start(self) -> "FakeClaudexorDaemon":  # pragma: no cover - unreachable
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - unreachable
        raise NotImplementedError


class PlaywrightUIClient:
    """Real-browser client over an isolated server's web UI (gateway/UI truth)."""

    def __init__(self, *_args, **_kwargs) -> None:
        raise NotImplementedError(_NOT_LANDED.format(
            name="PlaywrightUIClient", lane="gateway/UI-truth"))

    def open(self) -> "PlaywrightUIClient":  # pragma: no cover - unreachable
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - unreachable
        raise NotImplementedError
