"""Change the TC order on the dashboard; see how far it travels. Rolls back."""

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
	from upande_summer_flowers.summer_flowers import planning_api, sourcing
	from upande_summer_flowers.summer_flowers.crop_protocol import set_route_quantities
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import (
			build, space_at,
		)

	pick = None
	for p in frappe.get_all("Summer Flower Production Plan",
	                        filters={"docstatus": 0},
	                        fields=["name", "variety", "farm", "protocol",
	                                "new_plants_required"],
	                        order_by="modified desc", limit=80):
		if not (p.protocol and p.new_plants_required):
			continue
		beds, _b = space_at(p.farm)
		if not beds:
			continue
		v = frappe.get_doc("Crop Protocol Version", p.protocol)
		d = sourcing.route_plan(v)
		if d["has_route"] and d["method"] == "Purchase" and d["propagates"]:
			pick = (p, v)
			break
	if not pick:
		print("no usable draft plan")
		return
	p, v = pick
	print("plan %s -- %s at %s" % (p.name, p.variety, p.farm))

	for old in frappe.get_all("Summer Flower Procurement Plan",
	                          filters={"production_plan": p.name, "docstatus": 0},
	                          pluck="name"):
		frappe.delete_doc("Summer Flower Procurement Plan", old, force=True,
		                  ignore_permissions=True)
	q = frappe.get_doc("Summer Flower Procurement Plan", build(p.name))
	est = [r for r in q.requirements if (r.line_type or "") == "Establishment"]
	print("\nprocurement %s before:" % q.name)
	print("   establishment  %s plantlets, order by %s"
	      % (f"{cint(est[0].qty_to_order):,}" if est else "-",
	         est[0].order_by_date if est else "-"))
	print("   pool           %s mother plants" % f"{cint(q.pool_plants):,}")

	pd = frappe.get_doc("Summer Flower Production Plan", p.name)
	new_qty = cint(pd.tc_plants_to_order) * 2 or 5000
	new_date = frappe.utils.add_days(frappe.utils.nowdate(), 30)
	print("\nconfirming on the dashboard: %s plantlets, order by %s"
	      % (f"{new_qty:,}", new_date))
	res = planning_api.confirm_tc_choice(plan=p.name, tc_qty=new_qty,
	                                    order_date=new_date,
	                                    reason="cascade test")
	print("   carried through to: %s" % "; ".join(res["changed"]))

	q.reload()
	est = [r for r in q.requirements if (r.line_type or "") == "Establishment"]
	print("\nprocurement %s after:" % q.name)
	print("   establishment  %s plantlets, order by %s"
	      % (f"{cint(est[0].qty_to_order):,}" if est else "-",
	         est[0].order_by_date if est else "-"))
	print("   pool           %s mother plants" % f"{cint(q.pool_plants):,}")
	print("   units total    %s" % f"{cint(q.total_units_to_order):,}")
	print("   basis          %s" % q.sizing_basis)


	# and on through submit: the propagation plan and the calendar it feeds
	q.reload()
	frappe.message_log = []
	q.flags.ignore_permissions = True
	q.submit()
	print("\nafter approving the procurement plan:")
	for m in frappe.message_log:
		import json as _j
		try:
			m = _j.loads(m) if isinstance(m, str) else m
		except Exception:
			pass
		t = m.get("title") if isinstance(m, dict) else ""
		msg = m.get("message") if isinstance(m, dict) else m
		if t in ("Propagation plan", "Planting plan", "Not all of it can be stuck"):
			print("   [%s] %s" % (t, msg))
	pp = frappe.db.get_value("Summer Flower Propagation Plan",
	                         {"production_plan": p.name},
	                         ["name", "tc_plants_required", "tc_order_date"],
	                         as_dict=True)
	print("   propagation plan now holds: %s" % (pp,))
