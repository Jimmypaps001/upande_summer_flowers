# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Let the root items hold stock again.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.enable_root_items.main
	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.enable_root_items.main \\
		--kwargs "{'apply': 1}"

Every root item named on a material route is disabled on this site, so no stock
can be moved against one -- a Stock Entry refuses with "Item X is not active".
That matters more than it used to. Roots in store are netted off next season's
order, and a transfer out of the cold store into a propagation unit is now READ
as the withdrawal rather than typed in, so a disabled item means:

    the cold store always reads empty, so nothing is netted off,
    a shortfall can never be covered from roots already paid for, and
    the propagation unit has to type in a figure the ledger should have given.

Items are disabled on this site by the item approval guard, so this says plainly
which ones it is turning back on and leaves everything else alone.
"""

import traceback

import frappe
from frappe.utils import cint

LIFT_STAGE = "Roots"


def main(apply=0, variety=None):
	# bench execute swallows the real exception and retries the call as an
	# eval, which then reports "name 'upande_summer_flowers' is not defined".
	try:
		_run(apply, variety)
	except Exception:
		print(traceback.format_exc())


def _run(apply, variety):
	items = _root_items(variety)
	if not items:
		print("No material route names a root item.")
		return

	off = [i for i in items if cint(frappe.db.get_value("Item", i, "disabled"))]
	print("%d root items named on routes, %d of them disabled.\n"
	      % (len(items), len(off)))
	for i in sorted(items):
		bad = cint(frappe.db.get_value("Item", i, "disabled"))
		bins = frappe.db.count("Bin", {"item_code": i})
		print("   %s  %s   %d bins" % ("DISABLED" if bad else "active   ", i, bins))

	if not off:
		print("\nNothing to do.")
		return
	if not cint(apply):
		print("\nDry run. Re-run with --kwargs \"{'apply': 1}\" to enable the "
		      "%d disabled items above." % len(off))
		return

	for i in off:
		frappe.db.set_value("Item", i, "disabled", 0, update_modified=False)
	frappe.db.commit()
	print("\nEnabled %d items. Roots can now be received into a propagation "
	      "unit and read back off the ledger." % len(off))


def _root_items(variety=None):
	"""Every item named on a Roots row of a live material route."""
	from upande_summer_flowers.summer_flowers import crop_protocol as cp

	filters = {"version_status": "Active"}
	if variety:
		filters["variety"] = variety
	out = set()
	for name in frappe.get_all("Crop Protocol Version", filters=filters,
	                           pluck="name"):
		v = frappe.get_cached_doc("Crop Protocol Version", name)
		for r in (cp.route_rows(v) or []):
			if (r.get("stage") or "") == LIFT_STAGE and r.get("item"):
				if frappe.db.exists("Item", r.get("item")):
					out.add(r.get("item"))
	return sorted(out)
