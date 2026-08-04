# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Material Request as the crop input order sheet.

There is no separate order-sheet doctype. Material Request already models a
request for goods, carries this site's farm / business unit / request type, is
approved by being submitted, tracks how much has been ordered and received, and
turns into a Purchase Order. Duplicating that in a parallel doctype meant
maintaining an approval, a status field and a hand-off that Material Request
already has, and it kept the request outside every report and dashboard the
business already runs on Material Request.

So this module adds only what Material Request cannot work out for itself: given
a block, an area and a per-hectare application rate, how much of each input is
needed, and how much of that is already on the shelf.

The site has 11,609 Material Requests and a dozen other scripts on the doctype,
so every branch here exits immediately unless custom_sf_block is set.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

# Request Types on this site that mean "crop inputs".
INPUT_REQUEST_TYPES = ("Farm Inputs", "Chemical Issuing", "Fertiliser Issuing")

GROUP_FOR_TYPE = {
	"Chemical Issuing": ("CHEMICALS", "Chemical Mix"),
	"Fertiliser Issuing": ("Fertilizer",),
}


def validate_input_request(doc, method=None):
	"""Derive quantities for a block-scoped request. Everything else is native."""
	if not doc.get("custom_sf_block"):
		return
	_pull_context(doc)
	_check_period(doc)
	_set_quantities(doc)
	_check_item_groups(doc)


def _pull_context(doc):
	if doc.get("custom_sf_crop_cycle"):
		cyc = frappe.db.get_value(
			"Crop Cycle", doc.custom_sf_crop_cycle,
			["custom_block", "custom_area_planted_sqm", "farm",
			 "custom_is_summer_flower_cycle"], as_dict=True)
		if cyc and not cint(cyc.custom_is_summer_flower_cycle):
			frappe.throw(_("{0} is not a summer flower cycle.")
			             .format(doc.custom_sf_crop_cycle))
		if cyc:
			if cyc.custom_block and cyc.custom_block != doc.custom_sf_block:
				frappe.throw(_(
					"Crop cycle {0} is on block {1}, not {2}."
				).format(doc.custom_sf_crop_cycle, cyc.custom_block,
				         doc.custom_sf_block))
			if not flt(doc.custom_sf_area_ha) and flt(cyc.custom_area_planted_sqm):
				doc.custom_sf_area_ha = flt(cyc.custom_area_planted_sqm) / 10_000
			if not doc.get("custom_farm"):
				doc.custom_farm = cyc.farm
	if not doc.get("custom_farm"):
		doc.custom_farm = frappe.db.get_value("Block", doc.custom_sf_block, "farm")
	if not flt(doc.custom_sf_area_ha):
		# Fall back to the whole block: better an obvious over-estimate than a
		# silent zero, which would order nothing at all.
		doc.custom_sf_area_ha = flt(frappe.db.get_value(
			"Block", doc.custom_sf_block, "custom_net_area_ha"))
	if not doc.get("set_warehouse"):
		doc.set_warehouse = _default_store(doc)


def _default_store(doc):
	"""The farm's own chemical or fertiliser store, when the request type says which."""
	field = {"Chemical Issuing": "custom_chemical_store",
	         "Fertiliser Issuing": "custom_fertilizer_store"}.get(
		doc.get("custom_request_type"))
	if field and doc.get("custom_farm"):
		return frappe.db.get_value("Farm", doc.custom_farm, field)
	return None


def _check_period(doc):
	if doc.get("custom_sf_period_to") and doc.get("schedule_date") and \
			getdate(doc.custom_sf_period_to) < getdate(doc.schedule_date):
		frappe.throw(_("The period ends before the required-by date."))


def _set_quantities(doc):
	"""qty is the shortfall: what the area needs, less what is already on hand.

	A line whose rate is not given is left exactly as the user typed it, so an
	ordinary hand-entered line on a block-scoped request still behaves normally.
	"""
	area = flt(doc.custom_sf_area_ha)
	for r in doc.items:
		if not flt(r.get("custom_sf_rate_per_ha")):
			r.custom_sf_qty_required = 0
			r.custom_sf_qty_in_stock = 0
			continue
		apps = max(1, cint(r.get("custom_sf_applications")))
		required = flt(r.custom_sf_rate_per_ha) * area * apps
		on_hand = _stock(r.item_code, r.get("warehouse") or doc.get("set_warehouse"))
		r.custom_sf_qty_required = required
		r.custom_sf_qty_in_stock = on_hand
		shortfall = max(0.0, required - on_hand)
		# Material Request will not accept a zero-qty line, so a fully covered item
		# has to be told about rather than silently set to nothing.
		if shortfall <= 0:
			frappe.msgprint(
				_("{0}: stock on hand ({1}) already covers the {2} required. "
				  "Remove the line or reduce the rate.").format(
					r.item_code, on_hand, required),
				indicator="orange", alert=True)
			r.qty = required
		else:
			r.qty = shortfall


def _stock(item, warehouse):
	if not item:
		return 0.0
	if warehouse:
		rows = frappe.db.sql(
			"select sum(actual_qty) from tabBin where item_code=%s and warehouse=%s",
			(item, warehouse))
	else:
		rows = frappe.db.sql(
			"select sum(actual_qty) from tabBin where item_code=%s", (item,))
	return flt(rows[0][0]) if rows and rows[0][0] else 0.0


def _check_item_groups(doc):
	"""A fertiliser request should not quietly carry chemicals."""
	allowed = GROUP_FOR_TYPE.get(doc.get("custom_request_type"))
	if not allowed:
		return
	wrong = []
	for r in doc.items:
		if not flt(r.get("custom_sf_rate_per_ha")):
			continue
		group = frappe.db.get_value("Item", r.item_code, "item_group")
		if group and group not in allowed:
			wrong.append("%s (%s)" % (r.item_code, group))
	if wrong:
		frappe.throw(_(
			"{0} does not belong in {1} on a {2} request. Change the request type "
			"or remove the item."
		).format(", ".join(wrong[:4]), " / ".join(allowed), doc.custom_request_type))


# ---------------------------------------------------------------------------

@frappe.whitelist()
def build_input_request(block=None, crop_cycle=None, request_type=None,
                        schedule_date=None, period_to=None, seed_from=None,
                        items=None, business_unit=None):
	"""Create a draft Material Request for a block's inputs.

	Draft on purpose: submitting is the approval, and that is the requester's or
	the Farm Manager's decision, not this function's.

	Business unit, request type and the per-line purpose are mandatory on Material
	Request at this site and cannot be derived from a block, so they come from
	Summer Flower Settings rather than being guessed here.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	if not (block or crop_cycle):
		frappe.throw(_("Name a block or a crop cycle."))
	if crop_cycle and not block:
		block = frappe.db.get_value("Crop Cycle", crop_cycle, "custom_block")

	settings = frappe.get_cached_doc("Summer Flower Settings")
	request_type = request_type or settings.get("default_request_type") or "Farm Inputs"
	business_unit = business_unit or settings.get("default_business_unit")
	if not business_unit:
		frappe.throw(_(
			"Material Request needs a Business Unit at this site. Set a default in "
			"Summer Flower Settings, or pass one in."))
	purpose = settings.get("default_input_purpose") or _("Summer flower crop inputs")

	farm = frappe.db.get_value("Block", block, "farm")
	company = frappe.db.get_value("Farm", farm, "company") or \
		frappe.defaults.get_user_default("Company")

	rows = frappe.parse_json(items) if isinstance(items, str) else (items or [])
	if not rows and seed_from:
		# The most recent request for this block is the only structured precedent
		# there is -- the site has no input programme to read from.
		src = frappe.get_doc("Material Request", seed_from)
		rows = [{
			"item_code": r.item_code, "uom": r.uom,
			"rate_per_ha": flt(r.get("custom_sf_rate_per_ha")),
			"applications": cint(r.get("custom_sf_applications")) or 1,
			"rate": flt(r.rate),
		} for r in src.items if flt(r.get("custom_sf_rate_per_ha"))]
		if not rows:
			frappe.throw(_("{0} has no rate-per-hectare lines to copy.")
			             .format(seed_from))
	if not rows:
		frappe.throw(_("Add at least one item, or name a request to copy from."))

	mr = frappe.new_doc("Material Request")
	mr.material_request_type = "Purchase"
	mr.company = company
	mr.transaction_date = getdate()
	mr.schedule_date = getdate(schedule_date or frappe.utils.nowdate())
	mr.custom_farm = farm
	mr.custom_request_type = request_type
	mr.custom_business_unit = business_unit
	mr.custom_sf_block = block
	mr.custom_sf_crop_cycle = crop_cycle
	mr.custom_sf_period_to = getdate(period_to) if period_to else None
	for r in rows:
		mr.append("items", {
			"item_code": r.get("item_code"),
			"qty": 1,  # replaced by validate once the rate is applied
			"uom": r.get("uom") or frappe.db.get_value(
				"Item", r.get("item_code"), "stock_uom"),
			"schedule_date": mr.schedule_date,
			"rate": flt(r.get("rate")),
			"custom_sf_rate_per_ha": flt(r.get("rate_per_ha")),
			"custom_sf_applications": cint(r.get("applications")) or 1,
			"custom_purpose": r.get("purpose") or purpose,
		})
	mr.flags.ignore_permissions = True
	mr.insert()
	frappe.db.commit()
	return {
		"material_request": mr.name, "block": block, "area_ha": flt(mr.custom_sf_area_ha),
		"warehouse": mr.set_warehouse, "status": mr.status,
		"lines": [{
			"item": r.item_code, "uom": r.uom,
			"rate_per_ha": flt(r.custom_sf_rate_per_ha),
			"applications": cint(r.custom_sf_applications),
			"required": flt(r.custom_sf_qty_required),
			"in_stock": flt(r.custom_sf_qty_in_stock),
			"qty": flt(r.qty),
		} for r in mr.items],
	}


@frappe.whitelist()
def input_requests(block=None, farm=None, crop_cycle=None, docstatus=None):
	"""Block-scoped Material Requests, for the dashboard."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	f = {"custom_sf_block": ["is", "set"]}
	if block:
		f["custom_sf_block"] = block
	if farm:
		f["custom_farm"] = farm
	if crop_cycle:
		f["custom_sf_crop_cycle"] = crop_cycle
	if docstatus is not None:
		f["docstatus"] = cint(docstatus)
	rows = frappe.get_all(
		"Material Request", filters=f,
		fields=["name", "status", "docstatus", "transaction_date", "schedule_date",
		        "custom_sf_period_to", "custom_sf_block", "custom_sf_crop_cycle",
		        "custom_sf_area_ha", "custom_request_type", "custom_farm",
		        "set_warehouse", "per_ordered", "per_received", "company"],
		order_by="transaction_date desc, name desc")
	for r in rows:
		r["block_code"] = str(r.custom_sf_block or "").split(" - Block ").pop()
		r["lines"] = frappe.db.count("Material Request Item", {"parent": r.name})
	return {"requests": rows, "total": len(rows)}
