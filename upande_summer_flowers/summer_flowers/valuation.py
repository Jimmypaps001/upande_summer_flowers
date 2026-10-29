# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Prices come from the items, not from figures typed on plans and protocols.

A stem is valued at its variety Item's valuation; TC, plants and roots bought
in are costed at the bought item's valuation. The valuation is the stock
ledger's, so it moves as material is received rather than being re-typed.
"""

import frappe
from frappe.utils import flt


def item_valuation(item, company=None):
	"""The item's valuation rate: the Item's own, else what its stock is held at.

	Held stock is averaged over the warehouses that hold it, weighted by
	quantity; with none in stock, the last valuation the ledger recorded.
	"""
	if not item or not frappe.db.exists("Item", item):
		return 0.0
	rate = _valuation(item, company)
	if not rate and company:
		# This company holds none of it at a value; the item's valuation wherever
		# it is held is still the item's valuation, and better than a zero price.
		rate = _valuation(item, None)
	return rate


def _valuation(item, company):
	rate = flt(frappe.db.get_value("Item", item, "valuation_rate"))
	if rate:
		return rate
	wh = {"company": company} if company else {}
	cond, args = "", {"item": item}
	if company:
		cond, args["company"] = " and w.company = %(company)s", company
	row = frappe.db.sql("""
		select sum(b.actual_qty * b.valuation_rate) / nullif(sum(b.actual_qty), 0)
		from `tabBin` b join `tabWarehouse` w on w.name = b.warehouse
		where b.item_code = %(item)s and b.actual_qty > 0 and b.valuation_rate > 0""" + cond,
		args)
	if row and flt(row[0][0]):
		return flt(row[0][0])
	last = frappe.get_all("Stock Ledger Entry",
	                      filters=dict(item_code=item, is_cancelled=0, valuation_rate=[">", 0], **wh),
	                      fields=["valuation_rate"], order_by="posting_date desc, posting_time desc, creation desc",
	                      limit=1)
	return flt(last[0].valuation_rate) if last else 0.0


def item_cost(item, company=None):
	"""What a unit bought in costs: its valuation, else its Standard Buying price."""
	rate = item_valuation(item, company)
	if rate:
		return rate
	if not item:
		return 0.0
	return flt(frappe.db.get_value("Item Price", {"item_code": item, "buying": 1,
	                                              "price_list": "Standard Buying"},
	                               "price_list_rate"))


def bought_item(variety, stage):
	"""The Item a stage is bought as: '<variety> <suffix>' when the stage names a
	suffix (Roots) and that Item exists, else the variety."""
	from upande_summer_flowers.summer_flowers import stages

	suffix = stages.bought_item_suffix(stage)
	if suffix and variety and frappe.db.exists("Item", "%s %s" % (variety, suffix)):
		return "%s %s" % (variety, suffix)
	return variety


def stem_price(variety, company=None):
	"""What a stem of this variety is valued at: the variety Item's valuation."""
	return item_valuation(variety, company)
