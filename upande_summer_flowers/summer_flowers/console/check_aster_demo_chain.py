"""Approve the Aster protocol and walk it to a procurement plan."""

import frappe
from frappe.utils import cint


def run(apply=0):
	apply = cint(apply)
	try:
		_run(apply)
	except Exception:
		print(frappe.get_traceback())
	finally:
		if not apply:
			frappe.db.rollback()
			print("\n  (rolled back)")


def _run(apply):
	from upande_summer_flowers.summer_flowers import crop_protocol, sourcing
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import (
			build, methods_for,
		)

	d = frappe.get_doc("Crop Protocol", "Aster Pink Flash-Karen")
	print("protocol status: %s" % d.get("custom_sf_protocol_status"))
	if d.get("custom_sf_protocol_status") != "Approved":
		d.custom_sf_change_reason = ("Route corrected to TC -> Motherstock -> Plants, "
		                             "the route the categories document gives every "
		                             "Aster of this group.")
		d.flags.ignore_permissions = True
		d.save()
		crop_protocol.approve(d.name)
		print("   approved")
	v = frappe.db.get_value("Crop Protocol Version",
	                        {"crop_protocol": d.name, "is_current": 1}, "name")
	print("current version: %s" % v)
	ver = frappe.get_doc("Crop Protocol Version", v)
	print("   route: %s" % " -> ".join(r.stage for r in ver.material_route))
    
	dec = sourcing.route_plan(ver)
	print("\nwhat the route says:")
	print("   %s" % dec["reason"])
	print("   method=%s  bought as=%s  propagates=%s  in house=%s"
	      % (dec["method"], dec["entry_stage"], dec["propagates"],
	         ", ".join(dec["in_house"])))

	req = sourcing.requirement(ver, "TC", 117000, 8000)
	print("\nsizing 8,000 plants in the busiest week:")
	print("   kind          %s" % req["kind"])
	print("   cuttings/wk   %s" % f"{req['weekly_draw']:,}")
	print("   mother plants %s" % f"{req['pool']:,}")
	print("   TC to order   %s" % f"{req['units']:,}")
	print("   basis: %s" % req["basis"])

	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_production_plan.summer_flower_production_plan import (
			build_from_demand,
		)

	reg = frappe.db.get_value("Summer Flower Market Demand",
	                          {"variety": "Aster Pink Flash", "farm": "Karen"}, "name")
	if not reg:
		print("\nno demand register for Aster Pink Flash at Karen")
		return
	print("\nbuilding a plan on %s from %s" % (v, reg))
	plan = build_from_demand(reg, farm="Karen", season_start_year=2027)
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	print("   %s  protocol %s" % (plan, p.protocol))
	news = [b for b in p.plan_blocks if b.is_new_planting]
	print("   %s plantings · %s beds · %s plants · coverage %s%%"
	      % (len(news), sum(cint(b.beds) for b in news),
	         f"{sum(cint(b.plants) for b in news):,}", round(p.coverage_pct or 0, 1)))
	print("   TC block says: %s plantlets, order by %s"
	      % (f"{cint(p.tc_plants_to_order):,}", p.tc_order_by_date))
	p.flags.ignore_permissions = True
	p.submit()

	m = methods_for(plan)
	print("   dialog defaults: method=%s, bought as=%s"
	      % (m["decided"]["method"], m["decided"]["entry_stage"]))
	q = frappe.get_doc("Summer Flower Procurement Plan", build(plan))
	print("   built %s" % q.name)
	print("   method %s at %s · propagates here %s (%s)"
	      % (q.method, q.entry_stage, q.propagates_here, q.in_house_stages))
	print("   plants at field %s · units to order %s · pool %s"
	      % (f"{cint(q.total_plants_at_field):,}",
	         f"{cint(q.total_units_to_order):,}", f"{cint(q.pool_plants):,}"))
	print("   %s" % (q.sizing_basis or "-"))
	kinds = {}
	for r in q.requirements:
		kinds[r.line_type or "Planting"] = kinds.get(r.line_type or "Planting", 0) + 1
	print("   lines %s" % kinds)
