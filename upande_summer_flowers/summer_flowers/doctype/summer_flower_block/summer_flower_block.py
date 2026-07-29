# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate


class SummerFlowerBlock(Document):
	def validate(self):
		self.set_capacity()
		self.compute_coverage()

	def set_capacity(self):
		self.gross_area_sqm = (self.gross_area_ha or 0) * 10_000
		self.sqm_gross_per_bed = (
			self.gross_area_sqm / self.total_beds if self.total_beds else 0
		)

	# ---------------------------------------------------------------- coverage
	def compute_coverage(self):
		"""Coverage from plantings still standing on this block today."""
		rows = frappe.get_all(
			"Summer Flower Planting",
			filters={"block": self.name, "planting_status": ["!=", "Uprooted"]},
			fields=["name", "variety", "beds", "plants", "planned_uproot_date",
			        "actual_uproot_date"],
		)
		today = getdate(nowdate())

		beds = plants = 0
		varieties = {}
		for r in rows:
			end = getdate(r.actual_uproot_date or r.planned_uproot_date)
			if end < today:
				continue
			beds += r.beds or 0
			plants += r.plants or 0
			varieties[r.variety] = varieties.get(r.variety, 0) + (r.beds or 0)

		self.beds_occupied = beds
		self.beds_free = max(0, (self.total_beds or 0) - beds)
		self.coverage_pct = (beds / self.total_beds * 100) if self.total_beds else 0
		self.plants_standing = plants
		self.current_varieties = (
			"\n".join(f"{v}: {b} beds" for v, b in sorted(varieties.items())) or None
		)

		if self.total_beds and beds > self.total_beds:
			frappe.msgprint(
				_("Block {0} is over-planted: {1} beds standing against a capacity of {2}.").format(
					self.name, beds, self.total_beds
				),
				indicator="red",
				title=_("Over capacity"),
			)

	def recalculate_coverage(self):
		"""Recompute and persist coverage without a full validate cycle."""
		self.compute_coverage()
		self.db_set({
			"beds_occupied": self.beds_occupied,
			"beds_free": self.beds_free,
			"coverage_pct": self.coverage_pct,
			"plants_standing": self.plants_standing,
			"current_varieties": self.current_varieties,
		}, update_modified=False)


@frappe.whitelist()
def coverage_summary(farm=None):
	"""Block coverage across a farm, for the dashboard."""
	filters = {"block_status": "Active"}
	if farm:
		filters["farm"] = farm
	return frappe.get_all(
		"Summer Flower Block",
		filters=filters,
		fields=["name", "block_code", "farm", "gross_area_ha", "total_beds",
		        "beds_occupied", "beds_free", "coverage_pct", "plants_standing",
		        "current_varieties"],
		order_by="farm asc, block_code asc",
	)
