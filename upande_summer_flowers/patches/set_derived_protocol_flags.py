# Copyright (c) 2026, James Kiruga and contributors
"""Fill in the two flags the protocol form hides fields by."""

import frappe
from frappe.utils import cint

POOL_STAGES = ("Motherstock", "Cuttings (Own)", "Roots (Own)", "Tubers (Own)")


def execute():
	if not frappe.db.has_column("Crop Protocol", "custom_sf_harvest_is_scheduled"):
		return

	names = frappe.get_all("Crop Protocol", pluck="name")
	scheduled = pooled = 0
	for name in names:
		flushes = frappe.db.count("Summer Flower Protocol Flush",
		                          {"parent": name, "parenttype": "Crop Protocol"})
		stages = frappe.get_all("Crop Material Stage",
		                        filters={"parent": name,
		                                 "parenttype": "Crop Protocol"},
		                        pluck="stage")
		want_sched = 1 if flushes else 0
		want_pool = 1 if any((s or "") in POOL_STAGES for s in stages) else 0

		cur = frappe.db.get_value(
			"Crop Protocol", name,
			["custom_sf_harvest_is_scheduled", "custom_sf_raised_from_pool"],
			as_dict=True) or {}
		if cint(cur.get("custom_sf_harvest_is_scheduled")) != want_sched:
			frappe.db.set_value("Crop Protocol", name,
			                    "custom_sf_harvest_is_scheduled", want_sched,
			                    update_modified=False)
			scheduled += 1
		if cint(cur.get("custom_sf_raised_from_pool")) != want_pool:
			frappe.db.set_value("Crop Protocol", name,
			                    "custom_sf_raised_from_pool", want_pool,
			                    update_modified=False)
			pooled += 1

	frappe.db.commit()
	print("   harvest-is-scheduled set on %d protocols, raised-from-pool on %d"
	      % (scheduled, pooled))
