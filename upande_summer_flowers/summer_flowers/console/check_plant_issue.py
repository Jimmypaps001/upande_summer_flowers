"""Buy plants, plant them, and watch the stock go down. Rolls back."""

import frappe
from frappe.utils import cint, nowdate


def run():
	try:
		_run()
	except Exception:
		print(frappe.get_traceback())
	finally:
		frappe.db.rollback()
		print("\n  (rolled back)")


def _run():
	from upande_summer_flowers.summer_flowers import plant_issue

	print("stock entry type:", plant_issue.ensure_entry_type(),
	      "purpose", frappe.db.get_value("Stock Entry Type", plant_issue.ENTRY_TYPE,
	                                     "purpose"))

	cal = frappe.db.get_value("Planting Calendar",
	                          {"calendar_status": ["in", ("Approved", "Planted")]},
	                          "name")
	if not cal:
		cal = frappe.db.get_value("Planting Calendar", {}, "name")
	if not cal:
		print("no planting calendar on this site")
		return
	d = frappe.get_doc("Planting Calendar", cal)
	print("\n%s — %s at %s, %s plants in %s"
	      % (d.name, d.variety, d.farm, f"{cint(d.plants):,}", d.block or "—"))
	print("   actual planting: %s   stock entry: %s"
	      % (d.actual_planting_date or "—", d.stock_entry or "—"))

	item_ok = frappe.db.exists("Item", d.variety)
	print("   item exists: %s   disabled: %s"
	      % (bool(item_ok),
	         frappe.db.get_value("Item", d.variety, "disabled") if item_ok else "—"))
	print("   warehouse it would issue from: %s" % plant_issue.source_warehouse(d))

	if item_ok and frappe.db.get_value("Item", d.variety, "disabled"):
		frappe.db.set_value("Item", d.variety, "disabled", 0)
		print("   (enabled the item for this test)")
	# and put some stock where it will be issued from, so the ledger has it
	wh = plant_issue.source_warehouse(d)
	if wh:
		r = frappe.new_doc("Stock Entry")
		r.stock_entry_type = "Material Receipt"
		r.purpose = "Material Receipt"
		r.company = d.company
		r.append("items", {"item_code": d.variety, "qty": cint(d.plants) + 1000,
		                   "t_warehouse": wh, "basic_rate": 1})
		r.flags.ignore_permissions = True
		try:
			r.insert(); r.submit()
			print("   (stocked %s with %s for the test)"
			      % (wh, f"{cint(d.plants)+1000:,}"))
		except Exception as e:
			print("   (could not stock it: %s)" % str(e).split(chr(10))[0][:90])

	print("\nrecording the planting...")
	d.actual_planting_date = nowdate()
	d.flags.ignore_permissions = True
	frappe.message_log = []
	d.save()
	d.reload()
	print("   stock entry now: %s" % (d.stock_entry or "none"))
	import json as _j
    
	for m in frappe.message_log:
		try:
			m = _j.loads(m) if isinstance(m, str) else m
		except Exception:
			pass
		t = m.get("title") if isinstance(m, dict) else ""
		msg = m.get("message") if isinstance(m, dict) else m
		print("   [%s] %s" % (t, str(msg)[:180]))

	if d.stock_entry:
		se = frappe.get_doc("Stock Entry", d.stock_entry)
		print("\n   %s  type=%s purpose=%s docstatus=%s posting=%s"
		      % (se.name, se.stock_entry_type, se.purpose, se.docstatus,
		         se.posting_date))
		for r in se.items:
			print("      %s x %s out of %s" % (r.item_code, cint(r.qty), r.s_warehouse))
		print("   remarks: %s" % se.remarks)

	print("\nsaving again must not issue a second time:")
	d.save()
	d.reload()
	n = frappe.db.count("Stock Entry", {"remarks": ["like", "%%%s%%" % d.name]})
	print("   stock entries against this planting: %s" % n)
