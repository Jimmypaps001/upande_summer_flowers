"""Build a procurement plan and submit it, exactly as the button does."""

import frappe
from frappe.utils import cint


def run():
	try:
		_run()
	except Exception:
		print("RAISED:")
		print(frappe.get_traceback())
	finally:
		frappe.db.rollback()
		print("\n  (rolled back)")


def _run():
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_procurement_plan.summer_flower_procurement_plan import build

	plan = frappe.db.get_value("Summer Flower Production Plan",
	                           {"variety": "Aster Pink Flash", "farm": "Karen",
	                            "docstatus": 1}, "name", order_by="creation desc")
	for n in frappe.get_all("Summer Flower Procurement Plan",
	                        filters={"production_plan": plan, "docstatus": ["<", 2]},
	                        pluck="name"):
		d = frappe.get_doc("Summer Flower Procurement Plan", n)
		if d.docstatus == 1:
			d.flags.ignore_permissions = True
			d.cancel()
		frappe.delete_doc("Summer Flower Procurement Plan", n, force=True,
		                  ignore_permissions=True)

	name = build(plan)
	q = frappe.get_doc("Summer Flower Procurement Plan", name)
	print("built %s  docstatus=%s  units=%s  cycles=%s"
	      % (name, q.docstatus, cint(q.total_units_to_order), q.multiplication_cycles))
	frappe.message_log = []
	q.flags.ignore_permissions = True
	q.submit()
	print("submitted. docstatus=%s status=%s" % (q.docstatus, q.status))
	import json as _j
	for m in frappe.message_log:
		try:
			m = _j.loads(m) if isinstance(m, str) else m
		except Exception:
			pass
		t = m.get("title") if isinstance(m, dict) else ""
		msg = m.get("message") if isinstance(m, dict) else m
		print("   [%s] %s" % (t, str(msg)[:160]))
