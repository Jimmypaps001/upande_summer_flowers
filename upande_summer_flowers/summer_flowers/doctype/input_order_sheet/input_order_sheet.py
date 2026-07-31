# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Chemical and fertiliser order sheet for a block over a period.

There are no production BOMs on this site -- the only flower-related BOM is for a
packaging sachet -- and nothing models a per-block input requirement, so the sheet
carries its own item lines. Quantity is application rate per hectare times the
planted area times the number of applications in the period, and what is ordered
is the shortfall after stock on hand, so a full store does not generate a request.

Nothing reaches procurement without Farm Manager approval: the Material Request
is only created by send_to_procurement, which refuses to run before approval.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime

APPROVER_ROLES = ("Farm Manager", "Agriculture Manager", "System Manager")

GROUPS = {
	"Chemical": ("CHEMICALS", "Chemical Mix"),
	"Fertiliser": ("Fertilizer",),
	"Chemical and Fertiliser": ("CHEMICALS", "Chemical Mix", "Fertilizer"),
}


def _has_role(roles=APPROVER_ROLES):
	return bool(set(roles) & set(frappe.get_roles(frappe.session.user)))


class InputOrderSheet(Document):
	def validate(self):
		self.check_period()
		self.pull_context()
		self.set_quantities()
		self.roll_up()
		self.check_item_groups()

	def check_period(self):
		if getdate(self.to_date) < getdate(self.from_date):
			frappe.throw(_("The period ends before it starts."))

	def pull_context(self):
		if not self.currency:
			self.currency = frappe.db.get_value("Company", self.company,
			                                    "default_currency")
		if self.crop_cycle:
			cyc = frappe.db.get_value(
				"Crop Cycle", self.crop_cycle,
				["custom_block", "custom_area_planted_sqm", "farm"], as_dict=True)
			if cyc:
				self.block = self.block or cyc.custom_block
				self.farm = self.farm or cyc.farm
				if not flt(self.area_ha) and flt(cyc.custom_area_planted_sqm):
					self.area_ha = flt(cyc.custom_area_planted_sqm) / 10_000
		if self.block and not self.farm:
			self.farm = frappe.db.get_value("Block", self.block, "farm")
		if not flt(self.area_ha) and self.block:
			self.area_ha = flt(frappe.db.get_value(
				"Block", self.block, "custom_gross_area_ha"))
		self.default_store()

	def default_store(self):
		"""Stock comes from the farm's own chemical or fertiliser store.

		A mixed sheet is left for the user to point at one store, because netting
		two stores into one figure would hide which one is actually short.
		"""
		if self.warehouse or not self.farm:
			return
		field = {"Chemical": "custom_chemical_store",
		         "Fertiliser": "custom_fertilizer_store"}.get(self.sheet_type)
		if field:
			self.warehouse = frappe.db.get_value("Farm", self.farm, field)

	def check_item_groups(self):
		"""A fertiliser sheet should not quietly carry chemicals."""
		allowed = GROUPS.get(self.sheet_type)
		if not allowed:
			return
		wrong = [r.item for r in self.items
		         if r.item_group and r.item_group not in allowed]
		if wrong:
			frappe.throw(_(
				"{0} is not in {1} on a {2} sheet. Change the sheet type or remove "
				"the item."
			).format(", ".join(wrong[:4]), " / ".join(allowed), self.sheet_type))

	def set_quantities(self):
		area = flt(self.area_ha)
		for r in self.items:
			r.currency = self.currency
			if r.item:
				meta = frappe.db.get_value(
					"Item", r.item, ["item_name", "item_group", "stock_uom"],
					as_dict=True)
				if meta:
					r.item_name = meta.item_name
					r.item_group = meta.item_group
					if not r.uom:
						r.uom = meta.stock_uom
			r.qty_required = flt(r.rate_per_ha) * area * max(1, int(r.applications or 1))
			r.qty_in_stock = self._stock(r.item)
			# Order only the shortfall: a full store should not raise a request.
			r.qty_to_order = max(0.0, flt(r.qty_required) - flt(r.qty_in_stock))
			if not flt(r.rate) and r.item:
				r.rate = flt(frappe.db.get_value("Item", r.item, "last_purchase_rate"))
			r.amount = flt(r.qty_to_order) * flt(r.rate)

	def _stock(self, item):
		"""Stock on hand, across warehouses unless one is named.

		Aggregated in SQL rather than through get_value, which in v16 refuses a
		function passed as a select string.
		"""
		if not item:
			return 0.0
		if self.warehouse:
			rows = frappe.db.sql(
				"select sum(actual_qty) from tabBin where item_code = %s and warehouse = %s",
				(item, self.warehouse))
		else:
			rows = frappe.db.sql(
				"select sum(actual_qty) from tabBin where item_code = %s", (item,))
		return flt(rows[0][0]) if rows and rows[0][0] else 0.0

	def roll_up(self):
		self.total_qty_required = sum(flt(r.qty_required) for r in self.items)
		self.total_qty_to_order = sum(flt(r.qty_to_order) for r in self.items)
		self.total_amount = sum(flt(r.amount) for r in self.items)
		self.lines = len(self.items)

	# ------------------------------------------------------------- actions
	@frappe.whitelist()
	def submit_for_approval(self):
		if not self.items:
			frappe.throw(_("Add at least one item."))
		self.status = "Pending Approval"
		self.rejection_reason = None
		self.save()
		return self.status

	@frappe.whitelist()
	def approve(self):
		if not _has_role():
			frappe.throw(_("Only a Farm Manager can approve an order sheet."))
		if self.status != "Pending Approval":
			frappe.throw(_("Submit the sheet for approval first."))
		self.status = "Approved"
		self.approved_by = frappe.session.user
		self.approved_on = now_datetime()
		self.save()
		return self.status

	@frappe.whitelist()
	def reject(self, reason=None):
		if not _has_role():
			frappe.throw(_("Only a Farm Manager can reject an order sheet."))
		if not (reason or "").strip():
			frappe.throw(_("A rejection reason is required."))
		self.status = "Rejected"
		self.rejection_reason = reason
		self.save()
		return self.status

	@frappe.whitelist()
	def send_to_procurement(self):
		"""Raise a Material Request for the shortfall. Approval required first."""
		if self.status != "Approved":
			frappe.throw(_(
				"An order sheet must be approved by a Farm Manager before it goes "
				"to procurement."))
		if self.material_request and frappe.db.exists("Material Request",
		                                             self.material_request):
			return self.material_request
		lines = [r for r in self.items if flt(r.qty_to_order) > 0]
		if not lines:
			frappe.throw(_(
				"Nothing to order: stock on hand covers every line."))

		# This site makes farm, business unit and request type mandatory on
		# Material Request, and purpose mandatory on each line. They are captured
		# on the sheet so procurement routing is an explicit decision rather than
		# something guessed at the moment the request is raised.
		missing = [label for value, label in (
			(self.farm, _("Farm")),
			(self.business_unit, _("Business Unit")),
			(self.request_type, _("Request Type")),
			(self.purpose, _("Purpose")),
		) if not value]
		if missing:
			frappe.throw(_(
				"Material Request needs {0} at this site. Set {1} on the order "
				"sheet before sending it to procurement."
			).format(", ".join(missing), "them" if len(missing) > 1 else "it"))

		mr = frappe.new_doc("Material Request")
		mr.material_request_type = "Purchase"
		mr.company = self.company
		mr.transaction_date = getdate()
		mr.schedule_date = getdate(self.from_date)
		mr.custom_farm = self.farm
		mr.custom_business_unit = self.business_unit
		mr.custom_request_type = self.request_type
		for r in lines:
			mr.append("items", {
				"item_code": r.item,
				"qty": flt(r.qty_to_order),
				"uom": r.uom,
				"rate": flt(r.rate),
				"warehouse": self.warehouse,
				"schedule_date": getdate(self.from_date),
				"custom_purpose": self.purpose,
			})
		mr.flags.ignore_permissions = True
		mr.insert()
		self.db_set("material_request", mr.name)
		self.db_set("status", "Sent to Procurement")
		return mr.name

	@frappe.whitelist()
	def load_from_protocol(self):
		"""Seed lines from the previous approved sheet for this block.

		There is no input programme doctype to read from, so the most recent
		approved sheet is the only structured precedent available; rates are
		copied and quantities recomputed for this period's area.
		"""
		prev = frappe.get_all(
			"Input Order Sheet",
			filters={"block": self.block, "sheet_type": self.sheet_type,
			         "status": ["in", ("Approved", "Sent to Procurement")],
			         "name": ["!=", self.name or ""]},
			pluck="name", order_by="from_date desc", limit=1)
		if not prev:
			frappe.throw(_(
				"No approved sheet exists for block {0} yet, so there is nothing to "
				"copy. Add the items once and later sheets can seed from this one."
			).format(self.block))
		src = frappe.get_doc("Input Order Sheet", prev[0])
		self.set("items", [])
		for r in src.items:
			self.append("items", {
				"item": r.item, "uom": r.uom, "rate_per_ha": flt(r.rate_per_ha),
				"applications": r.applications, "rate": flt(r.rate),
				"notes": r.notes,
			})
		self.save()
		return {"copied_from": src.name, "lines": len(self.items)}
