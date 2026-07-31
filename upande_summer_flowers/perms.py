"""One place that decides who may call the Summer Flowers web endpoints.

Every `@frappe.whitelist()` function in this app used to guard only against
Guest, which is not an access check: any authenticated Desk User — a driver, a
warehouse clerk, somebody with a single unrelated role — could call the read
endpoints and receive the whole programme. The endpoints also read through
`frappe.get_all`, which does not apply permissions, so nothing further down
would have stopped them either.

The guard is the shared choke point, so the check belongs here rather than
repeated in sixteen callers.

Row-level filtering is a separate, larger job: `get_all` still returns every
farm's rows to anyone who clears this gate. Moving those reads to `get_list`
would apply User Permissions per farm. Until then this is a door, not a fence.
"""

from __future__ import annotations

import frappe
from frappe import _

# Everything in this app hangs off the demand register, and the roles that
# grant it are the roles that define a Summer Flowers user.
BASE_DOCTYPE = "Summer Flower Market Demand"


def require(ptype: str = "read", doctype: str | None = None, doc=None) -> None:
    """Throw unless the session user really holds `ptype` on `doctype`."""
    if frappe.session.user == "Guest":
        frappe.throw(_("Please sign in."), frappe.PermissionError)

    dt = doctype or BASE_DOCTYPE
    if not frappe.has_permission(dt, ptype, doc=doc):
        frappe.throw(
            _("You do not have {0} access to {1}.").format(_(ptype), _(dt)),
            frappe.PermissionError,
            title=_("Not permitted"))


def demo() -> None:
    """Self-check: the gate must refuse a user who holds no role here."""
    victim = "perms-selfcheck@example.com"
    if not frappe.db.exists("User", victim):
        frappe.get_doc({"doctype": "User", "email": victim, "first_name": "Perms",
                        "send_welcome_email": 0, "roles": []}).insert(
                            ignore_permissions=True)
    frappe.set_user(victim)
    try:
        require("read")
    except frappe.PermissionError:
        pass
    else:
        raise AssertionError("a role-less Desk User cleared the read gate")

    frappe.set_user("Administrator")
    require("read")          # must not throw
    require("write")
    print("perms demo OK: role-less user refused, Administrator allowed")
