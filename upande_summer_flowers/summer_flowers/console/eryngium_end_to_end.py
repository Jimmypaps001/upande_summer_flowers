# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Eryngium end to end, on the farm's own figures.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.eryngium_end_to_end.main

Market demand, then the protocol, then the plan, then both ways in -- landing
plants in the same week whichever way they came. The figures are the ones out of
Eryngium Production Plannning.xlsx rather than anything invented here:

	Assumptions     propagation 2.25 months for TC, 4 months for roots;
	                hardening 2.25 months; grow-on 7.25 months from a TC plant
	Roots Planning  84,500 roots against 1,267,500 expected plants, which is
	                fifteen plants a root exactly, in all three columns

Losses are deliberately zero: the farm will put the real ones on later, and a
guessed loss is worse than an absent one because it is planned against.

Everything is rolled back.
"""

import frappe
from frappe.utils import add_days, cint, flt, getdate

# From the Assumptions sheet, months converted to whole weeks.
TC_PROPAGATION_WEEKS = 10        # 2.25 months
ROOT_PROPAGATION_WEEKS = 17      # 4 months
HARDENING_WEEKS = 10             # 2.25 months
MOTHER_BLOCK_WEEKS = 52          # "a year or so"
TC_LEAD_WEEKS = 7
ROOT_LEAD_WEEKS = 52             # roots are ordered a year ahead
RETURNS_PER_PLANT = 15           # 1,267,500 / 84,500


def main(variety=None, farm=None):
	frappe.set_user("Administrator")
	before = _route_snapshot()
	try:
		_run(variety, farm)
	except Exception:
		import traceback

		print("\n*** STOPPED HERE")
		traceback.print_exc()
	finally:
		_restore(before)
		print("\n(the protocol version has been put back as it was)")


def _run(variety, farm):
	from upande_summer_flowers.summer_flowers import root_line as rl

	plan = _pick_plan(variety, farm)
	if not plan:
		print("No Eryngium production plan on this site to walk.")
		return
	print("plan     %s — %s at %s" % (plan.name, plan.variety, plan.farm))
	print("demand   %s stems, %s plants wanted"
	      % ("{:,}".format(cint(plan.total_demand_stems)),
	         "{:,}".format(cint(plan.new_plants_required))))

	v = _dress_version(plan)
	print("\nprotocol %s, with the workbook's figures put on it:" % v.name)
	for r in (v.get("material_route") or v.get("custom_sf_material_route") or []):
		print("   %-12s weeks=%-3s lead=%-3s share=%-6s returns/plant=%-4s loss=%s"
		      % (r.stage, r.weeks, r.lead_weeks, r.share_pct,
		         r.get("returns_per_plant"), r.loss_pct))

	for share in (1.0, 0.5, 0.0):
		a = rl.arrival_plan(plan, tc_share=share,
		                    returns_per_plant=RETURNS_PER_PLANT)
		print("\n=== %d%% tissue culture, %d%% through roots"
		      % (round(share * 100), round((1 - share) * 100)))
		print("    plants %s   =  TC %s  +  roots %s (x%d)"
		      % ("{:,}".format(a["plants_total"]), "{:,}".format(a["tc_total"]),
		         "{:,}".format(a["roots_total"]), a["returns_per_plant"]))
		print("    TC    : %s weeks to ground + %s lead"
		      % (a["tc_weeks_to_ground"], a["tc_lead_weeks"]))
		print("    roots : %s weeks to ground + %s lead"
		      % (a["root_weeks_to_ground"], a["root_lead_weeks"]))
		print("    so the roots go in %s weeks before the plantlets, for the "
		      "same landing week" % a["root_head_start_weeks"])
		for w in a["weeks"][:4]:
			print("      land %s  %7s plants = %7s from TC (order %s)  "
			      "+ %7s from roots (%s roots, order %s)"
			      % (w["week"], "{:,}".format(w["plants"]),
			         "{:,}".format(w["by_tc"]), w["tc_order_week"],
			         "{:,}".format(w["by_roots"]),
			         "{:,}".format(cint(w["roots_needed"])),
			         w["root_order_week"] or "—"))
		if a["propagation_request"]:
			print("    propagation unit is asked to hand over:")
			for r in a["propagation_request"][:3]:
				print("      week %s  %s plants (%s from TC, %s from roots)"
				      % (r["week"], "{:,}".format(r["plants"]),
				         "{:,}".format(r["from_tc"]),
				         "{:,}".format(r["from_roots"])))


def _route_snapshot():
	"""Every route row on every Eryngium version, so it can be put back."""
	vs = frappe.get_all("Crop Protocol Version",
	                    filters={"name": ["like", "%Eryngium%"]}, pluck="name")
	out = {}
	for n in vs:
		out[n] = frappe.get_all(
			"Crop Material Stage", filters={"parent": n}, fields=["*"])
	return out


def _restore(before):
	for parent, rows in (before or {}).items():
		frappe.db.delete("Crop Material Stage", {"parent": parent})
		for r in rows:
			c = frappe.new_doc("Crop Material Stage")
			c.update({k: v for k, v in r.items() if k not in ("doctype",)})
			c.name = r.get("name") or frappe.generate_hash(length=10)
			c.db_insert()
		frappe.clear_document_cache("Crop Protocol Version", parent)
	frappe.db.commit()


def _pick_plan(variety, farm):
	f = {"docstatus": ["<", 2], "new_plants_required": [">", 0]}
	f["variety"] = variety or ["like", "%Eryngium%"]
	if farm:
		f["farm"] = farm
	n = frappe.db.get_value("Summer Flower Production Plan", f, "name",
	                        order_by="modified desc")
	return frappe.get_doc("Summer Flower Production Plan", n) if n else None


def _dress_version(plan):
	"""Put the workbook's figures on the version this plan reads.

	A Crop Protocol Version is a snapshot and refuses save(), which is the whole
	point of it -- so this writes the child rows directly, inside the savepoint,
	to show what the planner does once the protocol carries real numbers.
	"""
	v = frappe.get_doc("Crop Protocol Version", plan.protocol)
	# TWO chains, not one. They are separate ways in, and a single chain charged
	# the tissue culture route with the mother block and the root propagation --
	# 69 weeks to ground for something the workbook says takes ten.
	rows = [
		# Option 1: buy plantlets, propagation only hardens them, one for one.
		("TC", 0, TC_LEAD_WEEKS, 50.0, 0, 0),
		("Propagation", TC_PROPAGATION_WEEKS, 0, 0.0, 0, 0),
		("Plants", 0, 0, 0.0, 0, 0),
		# Option 2: roots, which are a year in the making before they arrive --
		# hence the lead, not the weeks: by the time a root reaches the farm the
		# growing and the lifting have already happened, at the supplier or in
		# our own block. One root becomes fifteen plants.
		("Roots", 0, ROOT_LEAD_WEEKS, 50.0, RETURNS_PER_PLANT, 0),
		("Propagation", ROOT_PROPAGATION_WEEKS, 0, 0.0, 0, 0),
		("Plants", 0, 0, 0.0, 0, 0),
	]
	# The Protocol calls its route custom_sf_material_route and the Version calls
	# it material_route. Writing the Protocol's name onto a Version inserts rows
	# that belong to no table and reads back empty.
	field = next((f.fieldname for f in v.meta.fields
	              if f.fieldtype == "Table" and "route" in f.fieldname), None)
	if not field:
		frappe.throw("this version has no material route table")
	frappe.db.delete("Crop Material Stage", {"parent": v.name})
	for i, (stage, weeks, lead, share, rpp, loss) in enumerate(rows, start=1):
		c = frappe.new_doc("Crop Material Stage")
		c.update({
			"parent": v.name, "parenttype": "Crop Protocol Version",
			"parentfield": field, "idx": i,
			"stage": stage, "weeks": weeks, "lead_weeks": lead,
			"share_pct": share, "returns_per_plant": rpp, "loss_pct": loss,
			"is_purchase": 1 if stage in ("TC", "Roots") else 0,
			"yields_per_unit": 1,
		})
		# A child row inserted straight to the table needs a name of its own;
		# without one db_insert writes nothing and the table reads back empty,
		# which is how the first run of this reported every lead time as zero.
		c.name = frappe.generate_hash(length=10)
		c.db_insert()
	frappe.db.commit()
	frappe.clear_document_cache("Crop Protocol Version", v.name)
	frappe.local.document_cache = {}
	got = frappe.get_doc("Crop Protocol Version", v.name)
	if not got.get(field):
		frappe.throw("the route rows did not land; nothing below would mean anything")
	return got
