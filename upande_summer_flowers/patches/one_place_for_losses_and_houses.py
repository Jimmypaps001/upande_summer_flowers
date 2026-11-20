"""Put each road's losses on the road, and each house's rhythm in the table.

Two figures were being typed in two places. The protocol carried one rooting
success, one field establishment and one order allowance for the whole crop,
and every route then fell back to them -- so a protocol bought two ways showed
the same survival down both roads, which is almost never true. It carried
"suppliers available" and "a delivery every" beside a table that says the same
thing per house, and when the two disagreed nothing on screen said which the
plan obeyed. (It obeyed the table.)

The route and the table are now the only places to type either. The protocol
fields stay in the database as the fallback for anything not yet filled in,
but they are off the form, so this carries what they said onto the places that
will be read from now on. Nothing is deleted and no figure changes: a route
that already states its own is left exactly as it is.
"""

import frappe
from frappe.utils import cint, flt

LOSSES = (("rooting_success_pct", "custom_sf_rooting_success_pct"),
          ("field_establishment_pct", "custom_sf_field_establishment_pct"),
          ("order_loss_pct", "custom_sf_tc_order_loss_pct"))


def execute():
	protocols = frappe.get_all(
		"Crop Protocol", filters={"custom_is_summer_flower": 1},
		fields=["name", "custom_sf_rooting_success_pct",
		        "custom_sf_field_establishment_pct", "custom_sf_tc_order_loss_pct",
		        "custom_sf_suppliers_available", "custom_sf_supplier_cycles_per_year",
		        "custom_sf_supplier_lead_weeks"])
	roads = houses = 0
	for p in protocols:
		roads += _losses_onto_roads(p)
		houses += _houses_into_table(p)
	frappe.db.commit()
	print("one place for losses and houses: %d route(s) given their own "
	      "figures, %d house row(s) written" % (roads, houses))


def _losses_onto_roads(p):
	"""Each way takes the protocol's figures as its own, where it has none."""
	ways = frappe.get_all(
		"Crop Protocol Way",
		filters={"parent": p.name, "parenttype": "Crop Protocol"},
		fields=["name", "order_loss_pct", "rooting_success_pct",
		        "field_establishment_pct"],
		parent_doctype="Crop Protocol")
	done = 0
	for w in ways:
		# A road that already says something for itself is not touched. Saying
		# it again from the protocol would overwrite the more specific answer
		# with the less specific one.
		if any(flt(w.get(k)) for k, _src in LOSSES):
			continue
		mine = {k: flt(p.get(src)) for k, src in LOSSES}
		if not any(mine.values()):
			continue
		for k, v in mine.items():
			frappe.db.set_value("Crop Protocol Way", w.name, k, v,
			                    update_modified=False)
		done += 1
	return done


def _houses_into_table(p):
	"""The two numbers become that many rows, each on the stated rhythm.

	Nobody is invented: the rows carry no supplier name, because the protocol
	never knew one. A row with no name is still a house with a rhythm, and the
	farm puts the name on it when it knows it.
	"""
	if frappe.db.exists("Summer Flower Protocol Supplier",
	                    {"parent": p.name, "parenttype": "Crop Protocol"}):
		return 0
	count = max(0, min(12, cint(p.get("custom_sf_suppliers_available"))))
	cycle = max(0, min(52, cint(p.get("custom_sf_supplier_cycles_per_year"))))
	if not count or not cycle:
		return 0
	lead = cint(p.get("custom_sf_supplier_lead_weeks"))
	share = round(100.0 / count, 4)
	doc = frappe.get_doc("Crop Protocol", p.name)
	for i in range(count):
		doc.append("custom_sf_suppliers", {
			"supplier": None, "farm": None,
			"cycle_weeks": cycle, "lead_weeks": lead,
			# The last row carries the rounding, so the shares add to a hundred.
			"share_pct": share if i < count - 1 else round(100.0 - share * (count - 1), 4),
			"min_order_qty": 0, "preferred": 0,
		})
	# Written straight to the child table: saving the protocol would re-run
	# approval, take a version and stamp a change reason on 254 records for a
	# patch that changes nothing anybody typed.
	for row in doc.get("custom_sf_suppliers"):
		if not row.get("name") or row.get("__islocal"):
			row.parent = p.name
			row.parenttype = "Crop Protocol"
			row.parentfield = "custom_sf_suppliers"
			row.db_insert()
	return count
