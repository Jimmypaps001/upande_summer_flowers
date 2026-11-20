"""Give each route the losses its protocol's version was holding.

one_place_for_losses_and_houses carried the protocol's own loss fields onto
the routes. On a site where those fields were never filled it carried nothing,
and every route came out stating no loss at all -- which the planner reads as
"loses nothing", not as "nobody said". The figures were not missing, though:
they were on the Crop Protocol Version, which is what the plans were built
from, and which still has them.

So this reads the version in force and hands its figures to the routes of its
protocol. A route that already states its own is left alone: the road's own
answer is the more specific one and this must not argue with it. A figure the
version does not state is not invented -- nothing writes a zero.
"""

import frappe
from frappe.utils import flt

FIELDS = ("rooting_success_pct", "field_establishment_pct", "tc_order_loss_pct")
# The Way calls the order allowance by a shorter name than the Version does.
ONTO = {"rooting_success_pct": "rooting_success_pct",
        "field_establishment_pct": "field_establishment_pct",
        "tc_order_loss_pct": "order_loss_pct"}


def execute():
	protocols = frappe.get_all("Crop Protocol",
	                           filters={"custom_is_summer_flower": 1}, pluck="name")
	roads = touched = 0
	for name in protocols:
		figures = _version_in_force(name)
		if not figures:
			continue
		done = _onto_roads(name, figures)
		roads += done
		touched += 1 if done else 0
	frappe.db.commit()
	print("carried the versions' losses onto %d route(s) across %d protocol(s)"
	      % (roads, touched))


def _version_in_force(protocol):
	"""The Active version's figures, or the newest version's where none is.

	A protocol with two Active versions is a fault of its own, mended
	elsewhere; here the most recent simply wins, which is the one the planner
	would have picked anyway.
	"""
	rows = frappe.get_all(
		"Crop Protocol Version", filters={"crop_protocol": protocol},
		fields=["name", "version_status", "effective_from", "creation"] + list(FIELDS),
		order_by="effective_from desc, creation desc")
	if not rows:
		return None
	active = [r for r in rows if r.version_status == "Active"]
	v = (active or rows)[0]
	figures = {ONTO[f]: flt(v.get(f)) for f in FIELDS if flt(v.get(f))}
	return figures or None


def _onto_roads(protocol, figures):
	ways = frappe.get_all(
		"Crop Protocol Way",
		filters={"parent": protocol, "parenttype": "Crop Protocol"},
		fields=["name", "order_loss_pct", "rooting_success_pct",
		        "field_establishment_pct"],
		parent_doctype="Crop Protocol")
	done = 0
	for w in ways:
		if any(flt(w.get(k)) for k in ONTO.values()):
			continue
		for k, v in figures.items():
			frappe.db.set_value("Crop Protocol Way", w.name, k, v,
			                    update_modified=False)
		done += 1
	return done
