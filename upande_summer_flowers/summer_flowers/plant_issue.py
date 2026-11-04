# Copyright (c) 2026, James Kiruga and contributors
"""Plants leaving stock because they went in the ground."""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

# A Material Issue under a name the farm would use for it. The site already
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
	"""Where the plants for this planting are standing as stock."""
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
	"""Take this planting's plants out of stock."""
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
	se.from_warehouse = warehouse
	if se.meta.has_field("custom_farm"):
		se.custom_farm = planting.farm
	# The item's own stock UOM and a factor of one. Left out, Stock Entry refuses
	uom = frappe.db.get_value("Item", item, "stock_uom") or "Nos"
	# Plants that were bought carry a valuation from the receipt that brought them
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

# --------------------------------------------------------------- internal move
TRANSFER_TYPE = "Propagation Dispatch"
TRANSFER_PURPOSE = "Material Transfer"


def ensure_transfer_type():
	if frappe.db.exists("Stock Entry Type", TRANSFER_TYPE):
		return TRANSFER_TYPE
	doc = frappe.get_doc({"doctype": "Stock Entry Type", "name": TRANSFER_TYPE,
	                      "purpose": TRANSFER_PURPOSE})
	doc.flags.ignore_permissions = True
	doc.insert()
	return TRANSFER_TYPE


def propagation_warehouse(farm):
	"""Where the propagation unit holds what it has raised."""
	return frappe.db.get_single_value("Summer Flower Settings",
	                                  "propagation_warehouse")


@frappe.whitelist()
def dispatch_to_block(planting, qty=None, on_date=None):
	"""Move this planting's plants from the propagation unit to its block."""
	doc = frappe.get_doc("Planting Calendar", planting)
	if doc.get("dispatch_entry") and frappe.db.get_value(
			"Stock Entry", doc.dispatch_entry, "docstatus") == 1:
		frappe.msgprint(_("{0} already moved these plants on {1}.").format(
			doc.name, doc.dispatch_entry), indicator="blue")
		return {"stock_entry": doc.dispatch_entry}

	source = propagation_warehouse(doc.farm)
	if not source:
		frappe.throw(
			_("No propagation warehouse is named on Summer Flower Settings, so "
			  "there is nowhere to move these plants from. Set it, or record the "
			  "movement by hand."), title=_("No propagation warehouse"))
	target = doc.get("greenhouse") or frappe.db.get_value("Block", doc.block,
	                                                      "greenhouse")
	if not target:
		frappe.throw(
			_("{0} is not in a warehouse, so there is nowhere to move the plants "
			  "to. Give its block a greenhouse.").format(doc.block),
			title=_("No destination"))
	if source == target:
		frappe.throw(
			_("The propagation unit and {0} are the same warehouse, so nothing "
			  "moves.").format(doc.block), title=_("Nothing to move"))

	qty = cint(qty if qty is not None else doc.plants)
	if qty <= 0:
		frappe.throw(_("This planting has no plant count to move."))

	uom = frappe.db.get_value("Item", doc.variety, "stock_uom") or "Nos"
	valued = flt(frappe.db.get_value("Bin", {"item_code": doc.variety,
	                                         "warehouse": source},
	                                 "valuation_rate"))
	ensure_transfer_type()
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = TRANSFER_TYPE
	se.purpose = TRANSFER_PURPOSE
	se.company = doc.company
	se.posting_date = getdate(on_date or doc.get("actual_planting_date")
	                          or doc.planting_date or nowdate())
	se.set_posting_time = 1
	se.from_warehouse = source
	se.to_warehouse = target
	if se.meta.has_field("custom_farm"):
		se.custom_farm = doc.farm
	se.append("items", {
		"item_code": doc.variety, "qty": qty, "uom": uom, "stock_uom": uom,
		"conversion_factor": 1,
		"s_warehouse": source, "t_warehouse": target,
		"allow_zero_valuation_rate": 0 if valued else 1,
		"description": _("Raised here and sent to {0} for {1}").format(
			doc.block, doc.name),
	})
	se.remarks = _("Propagation dispatch for {0}").format(doc.name)
	se.flags.ignore_permissions = True
	try:
		se.insert()
		se.submit()
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Dispatch for %s" % doc.name)
		if se.get("name") and frappe.db.exists("Stock Entry", se.name):
			try:
				frappe.delete_doc("Stock Entry", se.name, force=True,
				                  ignore_permissions=True)
			except Exception:
				pass
		frappe.throw(
			_("The plants could not be moved: {0}").format(
				frappe.utils.strip_html(str(e)).split("\n")[0][:200]),
			title=_("Not moved"))

	doc.db_set("dispatch_entry", se.name, update_modified=False)
	frappe.msgprint(
		_("{0} moved {1} {2} from {3} to {4}.").format(
			se.name, "{:,}".format(qty), doc.variety, source, target),
		indicator="green", title=_("Plants dispatched"))
	return {"stock_entry": se.name, "qty": qty, "from": source, "to": target}
