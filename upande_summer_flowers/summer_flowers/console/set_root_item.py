# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Give the Roots row an Item, so roots in store are real stock.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_root_item.main
	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_root_item.main \\
		--kwargs "{'apply': 1}"

"Excess are stored to be used in future" only means something if next season's
order is netted against what is actually on the shelf, and that needs an Item to
count. Without one the card says so and nets nothing.

A root is not the plant it becomes, so it is its own item: fifteen roots and
fifteen plants are different things in different places at different times, and
one item for both would make the stock figure meaningless. The item is created
beside the variety's own item, taking its group and company, and named for the
variety so nobody has to guess which roots these are.
"""

import frappe
from frappe import _
from frappe.utils import cint

LIFT_STAGE = "Roots"
SUFFIX = " Roots"


def main(apply=0, variety="Eryngium"):
	apply = cint(apply)
	protocols = frappe.get_all(
		"Crop Protocol",
		filters={"custom_is_summer_flower": 1, "name": ["like", "%%%s%%" % variety]},
		fields=["name", "variety", "farm"])

	todo, have, novariety = [], [], []
	for p in protocols:
		rows = frappe.get_all("Crop Material Stage",
		                      filters={"parent": p.name, "stage": LIFT_STAGE},
		                      fields=["name", "item"])
		for r in rows:
			if r.item:
				have.append((p, r))
			elif p.variety and frappe.db.exists("Item", p.variety):
				todo.append((p, r, (p.variety or "") + SUFFIX))
			else:
				novariety.append(p)

	print("%d Roots row(s) already name an item" % len(have))
	for p, r in have[:6]:
		print("   kept %-44s %s" % (p.name[:44], r.item))
	print("%d Roots row(s) to point at a root item" % len(todo))
	seen = set()
	for p, r, item in todo:
		mark = "exists" if frappe.db.exists("Item", item) else "to create"
		if item not in seen:
			print("   %-9s %-34s  for %s" % (mark, item[:34], p.name[:34]))
			seen.add(item)
	if novariety:
		print("%d protocol(s) have no matching variety Item, so no root item can "
		      "be named:" % len(novariety))
		for p in novariety[:5]:
			print("   %s (variety %r)" % (p.name[:44], p.variety))

	if not apply:
		print("\n(dry run — pass --kwargs \"{'apply': 1}\" to write)")
		return

	made = 0
	for p, r, item in todo:
		if not frappe.db.exists("Item", item):
			_make_item(item, p.variety)
			made += 1
		frappe.db.set_value("Crop Material Stage", r.name, "item", item,
		                    update_modified=False)
		frappe.clear_document_cache("Crop Protocol", p.name)
	frappe.db.commit()
	print("\n%d item(s) created, %d Roots row(s) pointed at one." % (made, len(todo)))
	print("The protocols must be approved again before a plan sees it.")


def _make_item(name, like):
	"""A root item, taking what it can from the variety's own item."""
	src = frappe.get_doc("Item", like)
	doc = frappe.get_doc({
		"doctype": "Item",
		"item_code": name,
		"item_name": name,
		"item_group": src.item_group,
		"stock_uom": src.stock_uom or "Nos",
		"is_stock_item": 1,
		"is_purchase_item": 1,
		"is_sales_item": 0,
		"description": _("Roots of {0}. One root raises several plants at lift; "
		                 "what the season does not need is stored and drawn on "
		                 "later, which is why it is counted as its own stock and "
		                 "not as the plant it becomes.").format(like),
	})
	doc.flags.ignore_permissions = True
	doc.flags.ignore_mandatory = True
	doc.insert()
	return doc.name
