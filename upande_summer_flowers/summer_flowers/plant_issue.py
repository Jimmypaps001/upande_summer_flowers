# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Plants leaving stock because they went in the ground.

Plants are bought, so they arrive on a Purchase Receipt and sit in a warehouse
as stock -- see plant_receipt.py. Nothing took them out again. A planting was
recorded, a crop cycle was raised, flowers were forecast off it, and the
plantlets were still on the books in the store they had been delivered to. The
stock ledger said the farm was holding half a million plants it had planted.

So planting issues them: a Stock Entry of type Planting, which is a Material
Issue like any other consumption, against the planting that consumed them.

It is not raised on the plan, or on approval, or when a block is allocated. It
is raised when the plants actually go in the ground, because that is when they
stop being stock -- which is the day recorded in actual_planting_date.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

# A Material Issue under a name the farm would use for it. The site already
# names its entries this way -- Harvesting, Discard, Quarantine Rejects -- so a
# bare "Material Issue" on the ledger would say nothing about what happened.
ENTRY_TYPE = "Planting"
ENTRY_PURPOSE = "Material Issue"


def ensure_entry_type():
	"""The Stock Entry Type, created once and left alone after that."""
	if frappe.db.exists("Stock Entry Type", ENTRY_TYPE):
		return ENTRY_TYPE
	doc = frappe.get_doc({
		"doctype": "Stock Entry Type",
		"name": ENTRY_TYPE,
		"purpose": ENTRY_PURPOSE,
	})
	doc.flags.ignore_permissions = True
	doc.insert()
	return ENTRY_TYPE


def source_warehouse(planting):
	"""Where the plants for this planting are standing as stock.

	The warehouse they were delivered into, read off the receipt lines that point
	at this planting -- not the block's greenhouse, which is where the crop ends
	up and rarely where the plantlets were booked in. Falls back to the block's
	house, and then to the company default, so a planting whose material arrived
	before anyone was linking receipts still issues from somewhere real.
	"""
	for dt in ("Purchase Receipt", "Purchase Invoice"):
		row = frappe.db.sql("""
			select c.warehouse
			from `tab{child}` c join `tab{parent}` p on p.name = c.parent
			where c.custom_planting_calendar = %(planting)s
			  and p.docstatus = 1 and ifnull(c.warehouse, '') != ''
			order by p.posting_date desc limit 1
		""".format(child=dt + " Item", parent=dt),
			{"planting": planting.name}, as_dict=True)
		if row:
			return row[0].warehouse
	if planting.get("greenhouse"):
		return planting.greenhouse
	return frappe.db.get_value("Company", planting.company,
	                           "default_warehouse_for_sales_return") or None


def issue_for(planting, qty=None, on_date=None, throw=False):
	"""Take this planting's plants out of stock.

	Idempotent by design: a planting that already has a submitted issue is left
	alone, because saving the calendar again is not a second planting.
	"""
	if planting.get("stock_entry") and frappe.db.get_value(
			"Stock Entry", planting.stock_entry, "docstatus") == 1:
		return None

	item = planting.variety
	if not item or not frappe.db.exists("Item", item):
		msg = _("There is no Item called {0}, so the plants that went in cannot be "
		        "taken out of stock.").format(item)
		if throw:
			frappe.throw(msg, title=_("No item to issue"))
		return {"skipped": msg}
	if frappe.db.get_value("Item", item, "disabled"):
		msg = _("Item {0} is disabled, so nothing can be issued against it.").format(item)
		if throw:
			frappe.throw(msg, title=_("Item disabled"))
		return {"skipped": msg}

	qty = cint(qty if qty is not None else planting.plants)
	if qty <= 0:
		return {"skipped": _("This planting has no plant count to issue.")}

	warehouse = source_warehouse(planting)
	if not warehouse:
		msg = _("Nothing says which warehouse {0}'s plants were held in, so they "
		        "cannot be issued from one.").format(planting.name)
		if throw:
			frappe.throw(msg, title=_("No warehouse"))
		return {"skipped": msg}

	ensure_entry_type()
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = ENTRY_TYPE
	se.purpose = ENTRY_PURPOSE
	se.company = planting.company
	se.posting_date = getdate(on_date or planting.get("actual_planting_date")
	                          or planting.planting_date or nowdate())
	se.set_posting_time = 1
	# On the parent as well as the line. ERPNext fills a blank s_warehouse from the
	# item's own default during set_missing_values, so setting it only on the line
	# had the issue come out of General Store -- a warehouse this crop has never
	# been near -- and fail for want of stock that was sitting in the right one.
	se.from_warehouse = warehouse
	if se.meta.has_field("custom_farm"):
		se.custom_farm = planting.farm
	# The item's own stock UOM and a factor of one. Left out, Stock Entry refuses
	# the line outright -- "Row 1: UOM Conversion Factor is mandatory" -- which is
	# what stopped every issue before the warehouse was ever reached.
	uom = frappe.db.get_value("Item", item, "stock_uom") or "Nos"
	# Plants that were bought carry a valuation from the receipt that brought them
	# in. Plants that were raised here, or booked in before anyone costed them, do
	# not -- and Stock Entry refuses to post consumption it cannot value. A
	# planting is a physical fact and must be recordable either way, so where there
	# is no valuation the line is allowed a zero one and the entry says so, rather
	# than the planting going unrecorded to protect a number nobody has.
	valued = flt(frappe.db.get_value("Bin", {"item_code": item,
	                                         "warehouse": warehouse},
	                                 "valuation_rate"))
	se.append("items", {
		"item_code": item,
		"qty": qty,
		"uom": uom,
		"stock_uom": uom,
		"conversion_factor": 1,
		"s_warehouse": warehouse,
		"allow_zero_valuation_rate": 0 if valued else 1,
		"cost_center": frappe.db.get_value("Company", planting.company,
		                                   "cost_center"),
		"description": _("{0} planted in {1} on {2}").format(
			item, planting.block or planting.farm, se.posting_date),
	})
	se.remarks = _("Planted by {0}").format(planting.name) + (
		"" if valued else
		" — " + _("no valuation for {0} in {1}, so this is posted at zero value")
		.format(item, warehouse))
	se.flags.ignore_permissions = True
	try:
		se.insert()
		se.submit()
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Planting issue for %s" % planting.name)
		# insert() can succeed and submit() throw -- no stock, a closed period, a
		# rule this module has never heard of. The draft it leaves behind is not a
		# record of anything, and a second attempt would leave another.
		if se.get("name") and frappe.db.exists("Stock Entry", se.name):
			try:
				frappe.delete_doc("Stock Entry", se.name, force=True,
				                  ignore_permissions=True)
			except Exception:
				pass
		msg = _("The plants could not be taken out of stock: {0}").format(
			frappe.utils.strip_html(str(e)).split("\n")[0][:200])
		if throw:
			frappe.throw(msg, title=_("Not issued"))
		return {"skipped": msg}

	planting.db_set("stock_entry", se.name, update_modified=False)
	return {"stock_entry": se.name, "qty": qty, "warehouse": warehouse}


@frappe.whitelist()
def issue_plants(planting, qty=None, on_date=None):
	"""Issue by hand, from the calendar or from the crop cycle standing on it."""
	doc = frappe.get_doc("Planting Calendar", planting)
	out = issue_for(doc, qty=qty, on_date=on_date, throw=True)
	if not out:
		frappe.msgprint(_("{0} has already been issued on {1}.").format(
			doc.name, doc.stock_entry), indicator="blue")
		return {"stock_entry": doc.stock_entry}
	if out.get("skipped"):
		frappe.throw(out["skipped"], title=_("Not issued"))
	frappe.msgprint(
		_("{0} took {1} {2} out of {3}.").format(
			out["stock_entry"], "{:,}".format(out["qty"]), doc.variety,
			out["warehouse"]),
		indicator="green", title=_("Plants issued"))
	return out
