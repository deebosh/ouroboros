"""GET /api/widgets — the Widgets page card list, projected from the live loader.

Built purely from the in-memory extension snapshot: no skill discovery, no
stale-review reconcile, no schedule sync, no disk hashing, no writes
(DEVELOPMENT.md "Passive GET"). ``revision`` is the owning skill's live loader
``content_hash`` — a revision FACT for the page's change signature, not an
ETag and not a cache-busting token.
"""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from starlette.requests import Request
from starlette.responses import JSONResponse


class WidgetTab(TypedDict):
    """One Widgets card as served by ``GET /api/widgets``."""

    key: str
    skill: str
    tab_id: str
    title: str
    icon: str
    ws_prefix: str
    render: Dict[str, Any]
    span: int
    grid_span: int
    revision: str


class WidgetsResponse(TypedDict):
    ui_tabs: List[WidgetTab]


class ExtensionLiveSnapshot(TypedDict):
    """``extension_loader.snapshot()`` — the ``live`` block of ``GET /api/extensions``.

    Homed beside the Widgets projection that consumes it so ``gateway/contracts.py``
    stays within its module size band.
    """

    extensions: List[str]
    tools: List[str]
    routes: List[str]
    ws_handlers: List[str]
    ui_tabs: List[Dict[str, Any]]
    settings_sections: List[Dict[str, Any]]


def widget_tabs() -> List[WidgetTab]:
    """Project live UI tabs into Widgets cards stamped with the owning skill's revision."""
    from ouroboros.extension_loader import live_bundle_facts, snapshot

    tabs: List[WidgetTab] = []
    revisions: Dict[str, str] = {}
    for tab in snapshot().get("ui_tabs", []):
        skill = str(tab.get("skill") or "")
        if skill not in revisions:
            facts = live_bundle_facts(skill)
            revisions[skill] = facts[0] if facts else ""
        # The TypedDict IS the projection: every declared key except the stamped
        # revision comes straight from the snapshot tab (frame geometry stays in
        # ``render``, which is where the page reads it).
        card: Dict[str, Any] = {
            name: tab.get(name) for name in WidgetTab.__annotations__ if name != "revision"
        }
        card["revision"] = revisions[skill]
        tabs.append(card)  # type: ignore[arg-type]
    return tabs


async def api_widgets(_request: Request) -> JSONResponse:
    """GET /api/widgets — live widget cards from the loader snapshot only."""
    return JSONResponse({"ui_tabs": widget_tabs()})


__all__ = [
    "ExtensionLiveSnapshot",
    "WidgetTab",
    "WidgetsResponse",
    "api_widgets",
    "widget_tabs",
]
