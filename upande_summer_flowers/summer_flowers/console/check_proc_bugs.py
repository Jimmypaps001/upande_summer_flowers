"""Why the cycle count lands as zero, and what build() complains about."""

import json

import frappe
from frappe.utils import cint


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())
	finally:
		frappe.db.rollback()
		print("\n  (rolled back)")


def _run():
	from upande_summer_flowers.summer_flowers import sourcing
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import build

	plan = frappe.db.get_value("Summer Flower Production Plan",
	                           {"variety": "Aster Pink Flash", "farm": "Karen",
	                            "docstatus": 1}, "name", order_by="creation desc")
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)

	print("what requirement() hands back, cycles=4:")
	r = sourcing.requirement(v, "TC", cint(p.new_plants_required),
	                         cint(p.peak_weekly_sticking_planned), cycles=4)
	for k in ("kind", "cycles", "pool", "units", "calculated_units", "weekly_draw"):
		print("   %-18s %r" % (k, r.get(k)))

	for n in frappe.get_all("Summer Flower Procurement Plan",
	                        filters={"production_plan": plan, "docstatus": ["<", 2]},
	                        pluck="name"):
		d = frappe.get_doc("Summer Flower Procurement Plan", n)
		if d.docstatus == 1:
			d.flags.ignore_permissions = True
			d.cancel()
		frappe.delete_doc("Summer Flower Procurement Plan", n, force=True,
		                  ignore_permissions=True)

	frappe.message_log = []
	q = frappe.get_doc("Summer Flower Procurement Plan",
	                   build(plan, cycles=4, tc_qty=None))
	print("\nstored on %s:" % q.name)
	for f in ("multiplication_cycles", "calculated_units", "units_overridden",
	          "pool_plants", "weekly_draw", "total_units_to_order"):
		print("   %-24s %r" % (f, q.get(f)))

	print("\nwhat build() said while creating:")
	if not frappe.message_log:
		print("   (nothing)")
	for m in frappe.message_log:
		try:
			m = json.loads(m) if isinstance(m, str) else m
		except Exception:
			pass
		t = m.get("title") if isinstance(m, dict) else ""
		msg = m.get("message") if isinstance(m, dict) else m
		print("   [%s] %s" % (t, str(msg)[:220]))

	print("\nthe child table, first 3 planting lines and the establishment line:")
	print("   %-6s %-13s %-11s %-9s %-9s %s"
	      % ("line", "planting wk", "qty at field", "to order", "from pool", "order by"))
	shown = 0
	for r in q.requirements:
		kind = r.line_type or "Planting"
		if kind == "Planting" and shown >= 3:
			continue
		if kind == "Planting":
			shown += 1
		print("   %-6s %-13s %-11s %-9s %-9s %s"
		      % (kind[:6], r.planting_week or "—", f"{cint(r.qty_at_field):,}",
		         cint(r.qty_to_order), cint(r.drawn_from_pool), r.order_by_date or "—"))
