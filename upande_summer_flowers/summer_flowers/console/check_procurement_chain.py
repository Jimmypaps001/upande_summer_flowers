"""Buy TC, propagate it, plant what it becomes -- all three, from one submit.

Rolls back. Nothing is kept.
"""

import frappe


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())
	finally:
		frappe.db.rollback()
		print("\n  (rolled back -- nothing kept)")


def _run():
	from upande_summer_flowers.summer_flowers import sourcing
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import (
			build, methods_for,
		)

	from upande_summer_flowers.summer_flowers.crop_protocol import set_route_quantities
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import space_at

	pick = None
	for p in frappe.get_all("Summer Flower Production Plan",
	                        filters={"docstatus": ["<", 2]},
	                        fields=["name", "variety", "farm", "protocol",
	                                "new_plants_required",
	                                "peak_weekly_sticking_planned",
	                                "peak_sticking_week_planned"],
	                        order_by="modified desc", limit=80):
		if not (p.protocol and p.new_plants_required):
			continue
		beds, _b = space_at(p.farm)
		if not beds:
			continue
		v = frappe.get_doc("Crop Protocol Version", p.protocol)
		d = sourcing.route_plan(v)
		if not d["has_route"]:
			continue
		if d["method"] != "Purchase" or not d["propagates"]:
			# The farm's own shape, forced onto this fixture so the chain can be
			# walked: bought as TC, propagated through a motherstock. Rolled back.
			v.set("material_route", [])
			for stage, weeks, buy, lead in (("TC", 0, 1, 12), ("Motherstock", 8, 0, 0),
			                                ("Plants", 0, 0, 0)):
				v.append("material_route", {"row_type": "Stage", "stage": stage,
				                            "weeks": weeks, "is_purchase": buy,
				                            "lead_weeks": lead})
			v.max_multiplication_cycles = 4
			v.multiplication_factor_per_cycle = 1.0
			v.tc_order_loss_pct = 10.0
			v.cuttings_per_plant_per_week = 1.0
			v.cuttings_per_plant_required = 1.17
			v.cutting_reject_pct = 14.0
			set_route_quantities(v, native=True)
			for r in v.material_route:
				r.db_insert() if not r.name else frappe.db.set_value(
					r.doctype, r.name, {"yields_per_unit": r.yields_per_unit,
					                    "loss_pct": r.loss_pct,
					                    "is_standing": r.get("is_standing") or 0,
					                    "yields_per_week": r.get("yields_per_week") or 0},
					update_modified=False)
			frappe.db.set_value("Crop Protocol Version", v.name,
			                    {"max_multiplication_cycles": 4,
			                     "multiplication_factor_per_cycle": 1.0,
			                     "tc_order_loss_pct": 10.0,
			                     "cuttings_per_plant_per_week": 1.0,
			                     "cuttings_per_plant_required": 1.17,
			                     "cutting_reject_pct": 14.0}, update_modified=False)
			frappe.clear_document_cache("Crop Protocol Version", v.name)
			v = frappe.get_doc("Crop Protocol Version", v.name)
			d = sourcing.route_plan(v)
			if d["method"] != "Purchase" or not d["propagates"]:
				continue
		pick = (p, v, d)
		break
	if not pick:
		print("no usable plan")
		return
	p, v, d = pick
	print("plan      %s -- %s at %s" % (p.name, p.variety, p.farm))
	print("protocol  %s" % v.name)
	print("route     %s" % (v.get("route_summary") or "-"))
	print("verdict   %s" % d["reason"])
	print("          method=%s  bought as=%s  propagates=%s  in house=%s"
	      % (d["method"], d["entry_stage"], d["propagates"], ", ".join(d["in_house"])))

	m = methods_for(p.name)
	print("\ndialog would default to: method=%s, bought as=%s  (buyable: %s)"
	      % (m["decided"]["method"], m["decided"]["entry_stage"],
	         ", ".join(m["decided"]["buyable"])))

	# Build with no method at all -- the route should supply it.
	for old in frappe.get_all("Summer Flower Procurement Plan",
	                          filters={"production_plan": p.name, "docstatus": ["<", 2]},
	                          pluck="name"):
		dd = frappe.get_doc("Summer Flower Procurement Plan", old)
		if dd.docstatus == 1:
			dd.flags.ignore_permissions = True
			dd.cancel()
		frappe.delete_doc("Summer Flower Procurement Plan", old, force=True,
		                  ignore_permissions=True)
	name = build(p.name)
	q = frappe.get_doc("Summer Flower Procurement Plan", name)
	print("\nbuilt %s with no method passed:" % name)
	print("   method            %s" % q.method)
	print("   bought as         %s" % q.entry_stage)
	print("   propagates here   %s   through %s" % (q.propagates_here, q.in_house_stages))
	print("   plants at field   %s" % f"{q.total_plants_at_field:,}")
	print("   units to order    %s" % f"{q.total_units_to_order:,}")
	print("   pool              %s mother plants, %s cuttings a week"
	      % (f"{q.pool_plants or 0:,}", f"{q.weekly_draw or 0:,}"))
	print("   sizing basis      %s" % (q.sizing_basis or "-"))
	kinds = {}
	for r in q.requirements:
		kinds[r.line_type or "Planting"] = kinds.get(r.line_type or "Planting", 0) + 1
	print("   lines             %s" % kinds)

	before_mr = frappe.db.count("Material Request")
	before_pp = frappe.db.count("Summer Flower Propagation Plan")
	before_pc = frappe.db.count("Planting Calendar")
	frappe.message_log = []
	q.flags.ignore_permissions = True
	q.submit()
	print("\n   what it told the user:")
	for m in frappe.message_log:
		import json as _j
		try:
			m = _j.loads(m) if isinstance(m, str) else m
		except Exception:
			pass
		print("     [%s] %s" % (m.get("title") if isinstance(m, dict) else "",
		                        (m.get("message") if isinstance(m, dict) else m)))
	print("\nafter submit:")
	print("   material requests raised    %s" % (frappe.db.count("Material Request") - before_mr))
	print("   propagation plans built     %s" % (frappe.db.count("Summer Flower Propagation Plan") - before_pp))
	pp = frappe.db.get_value("Summer Flower Propagation Plan",
	                         {"production_plan": p.name}, ["name", "modified"])
	print("   propagation plan for it     %s" % (pp,))
	print("   planting calendar entries   %s" % (frappe.db.count("Planting Calendar") - before_pc))
