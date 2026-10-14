# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""The arrival endpoints the propagation phone calls.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.check_plant_arrivals.main

open_plant_orders is what the unit opens on, and draft_plant_receipt is what it
writes. Both are read and written from a phone with no desk in front of it, so
what matters is that a refusal says what is wrong rather than failing at save.

This builds its own purchase order, exercises both, and rolls the lot back.
Nothing it makes survives, so it is safe to run against a live site -- the one
thing it cannot prove there is the site's own mandatory fields, which it reports
instead of guessing at.
"""

import frappe
from frappe.utils import add_days, nowdate

from upande_summer_flowers.summer_flowers import plant_receipt as plr

PASS, FAIL = [], []


def _ok(name, cond, detail=""):
	(PASS if cond else FAIL).append((name, detail))


def main():
	try:
		_run()
	finally:
		frappe.db.rollback()

	for name, detail in PASS:
		print("  ok   %s%s" % (name, (" -- " + detail) if detail else ""))
	for name, detail in FAIL:
		print("  FAIL %s%s" % (name, (" -- " + detail) if detail else ""))
	print("\n%d passed, %d failed (everything rolled back)" % (len(PASS), len(FAIL)))
	return not FAIL


def _run():
	groups = plr.plant_item_groups()
	item = frappe.get_all("Item", filters={"item_group": ["in", list(groups)],
										   "is_stock_item": 1, "disabled": 0},
						  pluck="name", limit=1)
	supplier = frappe.get_all("Supplier", filters={"disabled": 0}, pluck="name", limit=1)
	if not item or not supplier:
		_ok("a plant item and a supplier exist", False,
			"item=%s supplier=%s" % (bool(item), bool(supplier)))
		return
	item, supplier = item[0], supplier[0]

	company = frappe.db.get_value("Item Default", {"parent": item}, "company") \
		or frappe.get_all("Company", pluck="name")[0]
	warehouses = frappe.get_all("Warehouse",
								filters={"is_group": 0, "disabled": 0, "company": company},
								pluck="name", limit=2)
	if len(warehouses) < 2:
		_ok("two warehouses to accept and reject into", False, company)
		return

	# The order the delivery will come against.
	po = frappe.get_doc({
		"doctype": "Purchase Order", "supplier": supplier, "company": company,
		"transaction_date": nowdate(), "schedule_date": add_days(nowdate(), 7),
		"items": [{"item_code": item, "qty": 1000, "rate": 10,
				   "warehouse": warehouses[0], "custom_purpose": "Arrival check",
				   "schedule_date": add_days(nowdate(), 7)}],
	})
	po.flags.ignore_permissions = True
	po.insert()
	po.submit()

	found = plr.open_plant_orders(supplier=supplier)
	names = [o["purchase_order"] for o in found["orders"]]
	_ok("an open order is offered", po.name in names, po.name)
	if po.name not in names:
		return

	order = [o for o in found["orders"] if o["purchase_order"] == po.name][0]
	line = order["lines"][0]
	_ok("the outstanding balance is what is left, not what was ordered",
		line["outstanding"] == 1000.0, str(line["outstanding"]))
	_ok("the site's mandatory receipt fields are named",
		isinstance(found.get("required_on_receipt"), list),
		", ".join(found.get("required_on_receipt") or []) or "none")

	note = "ARRIVAL-CHECK" if "supplier_delivery_note" in found["required_on_receipt"] else None

	# Rejecting with nowhere to put it is the mistake worth catching early.
	try:
		plr.draft_plant_receipt(
			po.name, [{"po_item": line["po_item"], "accepted": 10, "rejected": 5}],
			supplier_delivery_note=note)
		_ok("rejecting without a rejected warehouse is refused", False, "it was allowed")
	except frappe.ValidationError as e:
		_ok("rejecting without a rejected warehouse is refused", True, str(e)[:48])

	# Nothing to receive is refused too, rather than writing an empty receipt.
	try:
		plr.draft_plant_receipt(po.name, [{"po_item": line["po_item"], "accepted": 0}],
								supplier_delivery_note=note)
		_ok("an empty receipt is refused", False, "it was allowed")
	except frappe.ValidationError:
		_ok("an empty receipt is refused", True)

	res = plr.draft_plant_receipt(
		po.name,
		[{"po_item": line["po_item"], "accepted": 940, "rejected": 60,
		  "rejected_warehouse": warehouses[1], "rejection_reason": "damaged in transit"}],
		supplier_delivery_note=note)

	_ok("the receipt is left as a draft", res["docstatus"] == 0, str(res["docstatus"]))
	pr = frappe.get_doc("Purchase Receipt", res["name"])
	row = pr.items[0]
	_ok("accepted is the quantity kept", row.qty == 940.0, str(row.qty))
	_ok("rejected is held separately", row.rejected_qty == 60.0, str(row.rejected_qty))
	_ok("received is the two together", row.received_qty == 1000.0, str(row.received_qty))
	_ok("rejects go to their own warehouse",
		row.rejected_warehouse == warehouses[1], str(row.rejected_warehouse))
	_ok("the order line is still linked", bool(row.purchase_order_item),
		str(row.purchase_order_item))
	_ok("stock has not moved",
		not frappe.db.exists("Stock Ledger Entry", {"voucher_no": pr.name}))
