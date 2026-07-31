"""Keep every open dashboard in step with the documents behind it.

The dashboard is derived data. A protocol's flush curve, a demand week, an
approved plan, a motherstock date — change any of them and the coverage, the
bed count, the TC bill and the deadlines all move. Making someone press Reload
to find that out is how people act on a stale number.

Every doctype the dashboard reads bumps one version counter. The page watches
the counter and refetches. The dependency list is `_LIVE_DOCTYPES` in hooks.py,
which is the same list `dashboard.py` actually reads from — if one grows, the
other must.

The counter is the load-bearing part, not the socket. A browser polls it for
two cache reads and no query, so this works whether or not the realtime
process is running.
"""

from __future__ import annotations

import frappe

VERSION_KEY = "sf_dashboard_version"
LAST_KEY = "sf_dashboard_last_change"
CACHE_PREFIX = "sf_dashboard:"
EVENT = "summer_flowers_changed"

# What a change to each doctype means, in words a planner recognises.
REASON = {
    "Summer Flower Protocol": "a crop protocol",
    "Summer Flower Protocol Flush": "a flush curve",
    "Summer Flower Protocol Grade": "a grade split",
    "Summer Flower Market Demand": "the demand register",
    "Summer Flower Demand Week": "a demand week",
    "Summer Flower Production Plan": "a production plan",
    "Summer Flower Plan Week": "a production plan",
    "Summer Flower Plan Block": "a planned planting",
    "Summer Flower Planting": "a planting on the ground",
    "Summer Flower Block": "a block",
    "Summer Flower Motherstock Batch": "a motherstock batch",
    "Summer Flower Budget": "a budget",
    "Summer Flower Settings": "tissue-culture prices",
    "Summer Flower TC Price Band": "tissue-culture prices",
}


def version() -> int:
    """Monotonic stamp for 'the inputs to the dashboard'.

    Cache-resident because it is a hint, not a record. A cold cache restarts
    at 1, which every client reads as a change and refetches — the safe
    direction to be wrong in.
    """
    return int(frappe.cache().get_value(VERSION_KEY) or 1)


def bump(doctype: str | None = None, name: str | None = None) -> int:
    v = version() + 1
    frappe.cache().set_value(VERSION_KEY, v)
    if doctype:
        frappe.cache().set_value(LAST_KEY, {
            "version": v, "doctype": doctype, "name": name,
            "reason": REASON.get(doctype, "the plan"),
            "by": frappe.session.user, "at": frappe.utils.now(),
        })
    return v


def last_change() -> dict:
    return frappe.cache().get_value(LAST_KEY) or {}


def on_change(doc, method=None):
    """doc_events hook. One handler, every dependency."""
    # A child row saves through its parent; credit the parent so the message
    # names a document the user recognises.
    subject = getattr(doc, "parenttype", None) or getattr(doc, "doctype", None)
    if subject not in REASON:
        return
    v = bump(subject, getattr(doc, "parent", None) or getattr(doc, "name", None))
    frappe.publish_realtime(EVENT, {
        "version": v, "doctype": subject,
        "name": getattr(doc, "parent", None) or getattr(doc, "name", None),
        "reason": REASON.get(subject, "the plan"),
        "by": frappe.session.user,
    }, after_commit=True)


@frappe.whitelist()
def dashboard_version() -> dict:
    """Cheap poll for clients with no working socket."""
    return {"version": version(), "last": last_change(),
            "server_time": frappe.utils.now()}
