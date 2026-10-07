# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""The propagation units, as warehouses, with the capacity the workbook states.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_propagation_units.main
	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_propagation_units.main \\
		--kwargs "{'apply': 1}"

A propagation unit holds stock, so it is a warehouse. Capacity belongs on it
rather than in a single global figure, because the three units are not alike:
Kudenga takes both forms and holds 756,000, Bondet takes roots only and holds
192,500, and Plantech is an outside propagator that has never stated a figure.

Two kinds of number are kept apart on purpose:

  stated    what the capacity sheet says the unit holds. A limit.
  observed  the biggest week it has ever taken in, read off two years of
            deliveries. Evidence of what it has done, not a promise of what it
            can do -- so it is recorded separately and only used as a ceiling
            when nobody has stated one.

Every farm's material goes through more than one unit and the split is by form
-- tissue culture to Plantech and Kudenga, roots to Kudenga and Bondet -- so
there is no farm-to-unit link to make. Capacity is a pool, and the planner
measures the programme against the pool.
"""

import frappe
from upande_summer_flowers.summer_flowers.setup.propagation_units import (
	FIELDS, install,
)
from frappe.utils import cint

COMPANY = "Kaitet Group"

# From "Propagation Capacity Overview" in the Eryngium delivery book, and from
# two years of its own delivery rows for the observed figures.
UNITS = [
	{"warehouse_name": "Kudenga Propagation", "farm": "Kudenga",
	 "takes_tc": 1, "takes_roots": 1,
	 "capacity": 756_000, "tc": 648_000, "roots": 288_000,
	 "hold_tc": 6, "hold_roots": 10, "observed": 346_400,
	 "note": "27 tables at 28,000. Max tissue culture in trays 648,000, max "
	         "roots in trays 288,000."},
	{"warehouse_name": "Bondet Propagation", "farm": "Bondet",
	 "takes_tc": 0, "takes_roots": 1,
	 "capacity": 192_500, "tc": 0, "roots": 192_500,
	 "hold_tc": 6, "hold_roots": 8, "observed": 125_000,
	 "note": "22 trays, 7 troughs, 5 benches, 5 tunnels at 50 plants a tray. "
	         "Has only ever taken roots."},
	{"warehouse_name": "Plantech Propagation", "farm": None,
	 "takes_tc": 1, "takes_roots": 0,
	 "capacity": 0, "tc": 0, "roots": 0,
	 "hold_tc": 6, "hold_roots": 0, "observed": 179_176,
	 "note": "An outside propagator. The capacity sheet states no figure for it, "
	         "so none is invented; the planner has only the biggest week on "
	         "record to go on."},
]


def _parent():
	for name in (f"All Warehouses - {frappe.db.get_value('Company', COMPANY, 'abbr')}",):
		if frappe.db.exists("Warehouse", name):
			return name
	return None


def main(apply=0, company=None):
	apply = cint(apply)
	comp = company or COMPANY
	if not frappe.db.exists("Company", comp):
		print(f"no company {comp} on this site — nothing to do")
		return
	parent = _parent()
	print(f"company {comp}, parent warehouse {parent or '(none)'}\n")

	print("custom fields on Warehouse")
	for f in FIELDS["Warehouse"]:
		have = frappe.db.exists("Custom Field",
		                        {"dt": "Warehouse", "fieldname": f["fieldname"]})
		print(f"  {f['fieldname']:<36} {'already there' if have else 'to add'}")
	if apply:
		install()
		frappe.db.commit()
		print("  written")

	print("\nunits")
	for u in UNITS:
		name = f"{u['warehouse_name']} - {frappe.db.get_value('Company', comp, 'abbr')}"
		exists = frappe.db.exists("Warehouse", name)
		print(f"  {name:<34} {'exists' if exists else 'to create'}")
		print(f"      holds {u['capacity']:>9,}"
		      f"   tc {u['tc']:>9,}   roots {u['roots']:>9,}"
		      f"   weeks tc {u['hold_tc']} roots {u['hold_roots']}")
		print(f"      biggest week on record {u['observed']:,} (observed, not stated)")
		print(f"      {u['note']}")
		# Warehouse requires a farm here, and an outside propagator is not one.
		# Inventing a farm to get a warehouse saved would put a supplier into the
		# farm register, which is a worse lie than a missing unit.
		if not u["farm"] or not frappe.db.exists("Farm", u["farm"]):
			print(f"      SKIPPED — {'no farm named' if not u['farm'] else u['farm'] + ' is not a farm here'}"
			      f", and Warehouse requires one")
			continue
		if not apply:
			continue
		if exists:
			doc = frappe.get_doc("Warehouse", name)
		else:
			doc = frappe.new_doc("Warehouse")
			doc.warehouse_name = u["warehouse_name"]
			doc.company = comp
			if parent:
				doc.parent_warehouse = parent
		doc.custom_farm = u["farm"]
		doc.custom_sf_is_propagation_unit = 1
		doc.custom_sf_takes_tc = u["takes_tc"]
		doc.custom_sf_takes_roots = u["takes_roots"]
		doc.custom_sf_capacity_plants = u["capacity"]
		doc.custom_sf_tc_capacity_plants = u["tc"]
		doc.custom_sf_roots_capacity_plants = u["roots"]
		doc.custom_sf_observed_peak_intake = u["observed"]
		doc.custom_sf_hold_weeks_tc = u["hold_tc"]
		doc.custom_sf_hold_weeks_roots = u["hold_roots"]
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		print(f"      saved {doc.name}")

	if not apply:
		print("\n(dry run — nothing written)")
