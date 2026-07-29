# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt

import datetime

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, getdate

from upande_summer_flowers.summer_flowers.planning import iso_year_week


class SummerFlowerPlanting(Document):
	def before_naming(self):
		"""The name is built from the planting year and week, and naming happens
		before validate, so these must be set here or every row for a block
		collides on the same blank name."""
		if self.planting_date:
			self.planting_year, self.planting_week = iso_year_week(getdate(self.planting_date))

	def validate(self):
		self.protocol_doc = frappe.get_cached_doc("Summer Flower Protocol", self.protocol)
		self.check_variety_matches_protocol()
		self.set_size()
		self.set_dates()
		self.build_flush_projection()
		self.set_harvest_pattern()

	def on_update(self):
		self.refresh_block_coverage()

	def on_trash(self):
		self.refresh_block_coverage()

	# ------------------------------------------------------------------ checks
	def check_variety_matches_protocol(self):
		if self.protocol_doc.variety != self.variety:
			frappe.throw(
				_("Protocol {0} is for variety {1}, not {2}.").format(
					self.protocol, self.protocol_doc.variety, self.variety
				)
			)
		block_farm = frappe.db.get_value("Summer Flower Block", self.block, "farm")
		if block_farm and self.protocol_doc.farm != block_farm:
			frappe.throw(
				_("Protocol {0} belongs to farm {1} but block {2} is at {3}. "
				  "Climate parameters differ by farm — use the protocol for {3}.").format(
					self.protocol, self.protocol_doc.farm, self.block, block_farm
				)
			)

	# -------------------------------------------------------------------- size
	def set_size(self):
		p = self.protocol_doc
		self.plants = (self.beds or 0) * (p.plants_per_bed or 0)
		self.net_area_sqm = (self.beds or 0) * (p.sqm_net_per_bed or 0)
		self.gross_area_ha = ((self.beds or 0) * (p.sqm_gross_per_bed or 0)) / 10_000

	# ------------------------------------------------------------------- dates
	def set_dates(self):
		p = self.protocol_doc
		planted = getdate(self.planting_date)
		self.planting_year, self.planting_week = iso_year_week(planted)

		self.sticking_date = add_days(planted, -7 * (p.sticking_to_planting_weeks or 0))
		self.pinch_date = add_days(planted, 7 * (p.weeks_to_pinch or 0))
		self.planned_uproot_date = add_days(planted, 7 * (p.total_weeks_in_ground or 0))

	def end_date(self):
		"""The date this planting stops producing."""
		if self.actual_uproot_date:
			return getdate(self.actual_uproot_date)
		return getdate(self.planned_uproot_date)

	# ------------------------------------------------------------------ flushes
	def build_flush_projection(self):
		"""One row per flush that lands before the planting is uprooted.

		Existing harvested rows keep their actuals so the projection can be compared
		against what really came off the block.
		"""
		actuals = {
			r.flush_number: (r.is_harvested, r.actual_stems) for r in self.flush_projection
		}
		planted = getdate(self.planting_date)
		end = self.end_date()

		self.flush_projection = []
		for offset, stems_per_plant in self.protocol_doc.flush_offsets():
			harvest = planted + datetime.timedelta(weeks=offset)
			if harvest > end:
				break
			year, week = iso_year_week(harvest)
			flush_no = len(self.flush_projection) + 1
			was_harvested, actual = actuals.get(flush_no, (0, 0))
			self.append("flush_projection", {
				"flush_number": flush_no,
				"harvest_date": harvest,
				"year": year,
				"week_no": week,
				"stems_per_plant": stems_per_plant,
				"projected_stems": int(round(stems_per_plant * (self.plants or 0))),
				"is_harvested": was_harvested,
				"actual_stems": actual,
			})

	def set_harvest_pattern(self):
		rows = self.flush_projection
		self.lifetime_stems = sum((r.projected_stems or 0) for r in rows)
		if rows:
			self.first_harvest_year = rows[0].year
			self.first_harvest_week = rows[0].week_no
			# 4 x 13 = 52, so the same few weeks recur every year until uproot.
			family = sorted({r.week_no for r in rows})
			self.harvest_week_family = ", ".join(f"wk{w}" for w in family)
		else:
			self.first_harvest_year = self.first_harvest_week = None
			self.harvest_week_family = None

	# ---------------------------------------------------------------- coverage
	def refresh_block_coverage(self):
		if self.block and frappe.db.exists("Summer Flower Block", self.block):
			frappe.get_doc("Summer Flower Block", self.block).recalculate_coverage()

	# ----------------------------------------------------------------- queries
	def production_by_week(self, only_projected=False):
		"""{(year, week): stems} this planting contributes."""
		out = {}
		for r in self.flush_projection:
			stems = r.projected_stems or 0
			if not only_projected and r.is_harvested:
				stems = r.actual_stems or 0
			out[(r.year, r.week_no)] = out.get((r.year, r.week_no), 0) + stems
		return out


def standing_plantings(farm, variety, as_of=None):
	"""Plantings that are on the ground (or committed) for this farm and variety."""
	as_of = getdate(as_of or frappe.utils.nowdate())
	names = frappe.get_all(
		"Summer Flower Planting",
		filters={
			"farm": farm,
			"variety": variety,
			"planting_status": ["!=", "Uprooted"],
		},
		pluck="name",
	)
	out = []
	for name in names:
		doc = frappe.get_doc("Summer Flower Planting", name)
		if doc.end_date() >= as_of:
			out.append(doc)
	return out
