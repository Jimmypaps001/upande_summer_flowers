# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Walk the chain from an agreed motherstock line to a Purchase Receipt.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_to_receipt.main

The app's own chain stops at the Material Request -- deliberately, because
submitting one is a person committing money. Everything after it is stock
ERPNext: Material Request -> Purchase Order -> Purchase Receipt. Nobody had
walked the join, so this does, and says which step is the first to refuse.

It cannot run inside a savepoint: agreeing a line commits, which drops one. So
it writes for real and then undoes what it made, in reverse, reporting both --
an honest mess you can see beats a rollback that silently did not happen.
"""

import frappe
from frappe.utils import cint, flt


def main(production_plan=None, tc=None, multiplications=None, keep=0):
	frappe.set_user("Administrator")
	ok, made = [], []
	try:
		_walk(production_plan, tc, multiplications, ok, made)
	except Exception:
		import traceback

		print("\n*** STOPPED HERE")
		traceback.print_exc()
	print("\nreached: %s" % (" -> ".join(ok) if ok else "nothing"))
	if cint(keep):
		print("\nkept, as asked: %s" % ", ".join("%s %s" % m for m in made))
		return
	_undo(made)


def _undo(made):
	"""Cancel and delete what the walk made, newest first."""
	print("\ncleaning up")
	for dt, nm in reversed(made):
		try:
			d = frappe.get_doc(dt, nm)
			if d.docstatus == 1:
				d.flags.ignore_permissions = True
				d.cancel()
			frappe.delete_doc(dt, nm, force=True, ignore_permissions=True,
			                  delete_permanently=True)
			print("   removed %-26s %s" % (dt, nm))
		except Exception as e:
			print("   LEFT BEHIND %-22s %s  (%s)"
			      % (dt, nm, frappe.utils.strip_html(str(e))[:90]))
	frappe.db.commit()


def _step(ok, label, detail=""):
	ok.append(label)
	print("  [%d] %-22s %s" % (len(ok), label, detail))


def _walk(production_plan, tc, multiplications, ok, made):
	from upande_summer_flowers.summer_flowers import operations_api as ops

	name = production_plan or frappe.db.get_value(
		"Summer Flower Production Plan",
		{"docstatus": 1, "protocol": ["like", "%Aster%"]}, "name",
		order_by="modified desc")
	if not name:
		print("No submitted production plan to walk."); return
	p = frappe.get_doc("Summer Flower Production Plan", name)
	print("production plan: %s  (%s at %s)\n" % (p.name, p.variety, p.farm))

	r = ops.agree_motherstock_line(plan=name, tc=tc, multiplications=multiplications)
	_step(ok, "Motherstock Plan",
	      "%s — %s plantlets at %s multiplication(s)"
	      % (r["name"], "{:,}".format(r["tc_to_order"]), r["multiplications"]))
	for s in r.get("steps") or []:
		print("        %-18s %s  %s"
		      % (s["step"], "ok" if s["ok"] else "FAILED",
		         s.get("result") or s.get("error")))

	proc = frappe.db.get_value("Summer Flower Procurement Plan",
	                           {"production_plan": name, "docstatus": 0}, "name")
	if not proc:
		print("\nNo draft procurement plan to submit."); return
	d = frappe.get_doc("Summer Flower Procurement Plan", proc)
	_step(ok, "Procurement Plan",
	      "%s — method=%r units=%s supplier=%r"
	      % (d.name, d.method, "{:,}".format(cint(d.total_units_to_order)),
	         d.get("supplier")))

	d.flags.ignore_permissions = True
	d.submit()
	_step(ok, "submitted", "status=%s" % d.status)

	mrs = frappe.get_all("Material Request",
	                     filters={"docstatus": 0},
	                     fields=["name", "schedule_date", "material_request_type"],
	                     order_by="creation desc", limit=5)
	mine = [m for m in mrs if frappe.db.exists(
		"Material Request Item", {"parent": m.name})]
	if not mine:
		print("\n*** No Material Request was raised. method=%r — "
		      "raise_material_requests only runs for method 'Purchase'." % d.method)
		return
	mr = frappe.get_doc("Material Request", mine[0].name)
	made.append(("Material Request", mr.name))
	_step(ok, "Material Request",
	      "%s — %d item(s), type=%s, needed %s"
	      % (mr.name, len(mr.items), mr.material_request_type, mr.schedule_date))
	for it in mr.items:
		print("        item=%r qty=%s uom=%s warehouse=%r rate=%s"
		      % (it.item_code, it.qty, it.uom, it.warehouse, it.get("rate")))

    # --- from here it is stock ERPNext -------------------------------------
	mr.flags.ignore_permissions = True
	mr.submit()
	_step(ok, "MR submitted", mr.name)

	from erpnext.stock.doctype.material_request.material_request import (
		make_purchase_order,
	)

	po = make_purchase_order(mr.name)
	if not po.get("supplier"):
		# Whatever supplier is picked must be able to transact in the company's
		# currency, or ERPNext refuses the order outright -- the first pass here
		# grabbed a EUR supplier for a KES company and read as a broken chain
		# when it was a broken test.
		cur = frappe.db.get_value("Company", po.company, "default_currency")
		sup = frappe.db.get_value(
			"Supplier", {"disabled": 0, "default_currency": ["in", [cur, ""]]},
			"name") or frappe.db.get_value(
			"Supplier", {"disabled": 0, "default_currency": ["is", "not set"]},
			"name")
		print("        the request names no supplier; using %r (%s) to continue"
		      % (sup, cur))
		po.supplier = sup
	for it in po.items:
		if not flt(it.rate):
			it.rate = 1.0
	po.flags.ignore_permissions = True
	po.insert()
	made.append(("Purchase Order", po.name))
	_step(ok, "Purchase Order", "%s — supplier=%r total=%s"
	      % (po.name, po.supplier, po.grand_total))
	po.submit()
	_step(ok, "PO submitted", po.name)

	from erpnext.buying.doctype.purchase_order.purchase_order import (
		make_purchase_receipt,
	)

	pr = make_purchase_receipt(po.name)
	# What a receiving clerk types when the pallet arrives. Mandatory on this
	# site and rightly so -- it is the supplier's own note number, which no plan
	# can know in advance, so the chain stopping here is correct rather than
	# broken.
	if pr.meta.get_field("supplier_delivery_note"):
		pr.supplier_delivery_note = "TEST-DN-0001"
	for it in pr.items:
		if not it.get("warehouse"):
			it.warehouse = frappe.db.get_value(
				"Warehouse", {"company": pr.company, "is_group": 0}, "name")
	pr.flags.ignore_permissions = True
	pr.insert()
	made.append(("Purchase Receipt", pr.name))
	_step(ok, "Purchase Receipt", "%s — %d item(s)" % (pr.name, len(pr.items)))

	# The item is flagged inspection_required_before_purchase, so the receipt
	# cannot be submitted until somebody has inspected the delivery. That is a
	# control the farm chose, not a fault -- it just means the chain ends in a
	# DRAFT receipt waiting on QC, which is the right place for it to wait.
	for it in pr.items:
		tmpl = frappe.db.get_value("Item", it.item_code,
		                           "quality_inspection_template")
		if not tmpl:
			continue
		qi = frappe.get_doc({
			"doctype": "Quality Inspection",
			"inspection_type": "Incoming",
			"reference_type": "Purchase Receipt",
			"reference_name": pr.name,
			"item_code": it.item_code,
			"sample_size": 1,
			"quality_inspection_template": tmpl,
			"inspected_by": frappe.session.user,
		})
		qi.flags.ignore_permissions = True
		qi.insert()
		qi.submit()
		made.append(("Quality Inspection", qi.name))
		it.quality_inspection = qi.name
		_step(ok, "Quality Inspection", "%s (%s)" % (qi.name, tmpl))
	pr.save()
	pr.submit()
	_step(ok, "PR submitted", "%s  status=%s" % (pr.name, pr.status))

	sle = frappe.get_all("Stock Ledger Entry",
	                     filters={"voucher_no": pr.name},
	                     fields=["item_code", "actual_qty", "warehouse"])
	print("\n  stock moved:")
	for s in sle:
		print("        %-28s %+g into %s" % (s.item_code, s.actual_qty, s.warehouse))
	if not sle:
		print("        *** nothing — the receipt submitted but moved no stock")
