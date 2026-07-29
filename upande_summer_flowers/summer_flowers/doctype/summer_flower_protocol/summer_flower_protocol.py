# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt

import math

import frappe
from frappe import _
from frappe.model.document import Document

WEEKS_PER_YEAR = 52


class SummerFlowerProtocol(Document):
	def validate(self):
		self.set_geometry()
		self.set_flush_schedule()
		self.set_cycle_totals()
		self.set_motherstock()
		self.set_grades_and_losses()

	# ------------------------------------------------------------------ geometry
	def set_geometry(self):
		if not self.plants_per_sqm_net:
			frappe.throw(_("Plants per m² (net) is required."))
		if not self.net_gross_ratio:
			self.net_gross_ratio = 0.8

		self.sqm_net_per_bed = (self.plants_per_bed or 0) / self.plants_per_sqm_net
		self.sqm_gross_per_bed = self.sqm_net_per_bed / self.net_gross_ratio
		self.plants_per_sqm_gross = self.plants_per_sqm_net * self.net_gross_ratio
		self.min_planting_plants = (self.min_planting_beds or 0) * (self.plants_per_bed or 0)

	# --------------------------------------------------------------------- flush
	def set_flush_schedule(self):
		"""Order the flushes and fill in the offsets each one lands on."""
		rows = sorted(self.flush_schedule, key=lambda r: r.flush_number or 0)
		interval = self.flush_interval_weeks or 0

		for idx, row in enumerate(rows, start=1):
			row.idx = idx
			row.flush_number = idx
			# A blank interval means "regular cadence" -- derive it.
			if not row.weeks_from_pinch and interval:
				row.weeks_from_pinch = interval * idx
			row.weeks_from_planting = (self.weeks_to_pinch or 0) + (row.weeks_from_pinch or 0)
			row.harvest_week_of_year = "+{0}".format(
				row.weeks_from_planting + (self.calendar_rounding_weeks or 0)
			)

		self.flush_schedule = rows

	def set_cycle_totals(self):
		rows = self.flush_schedule
		self.total_flushes = len(rows)
		self.total_stems_per_plant_life = sum((r.stems_per_plant or 0) for r in rows)

		last = max((r.weeks_from_pinch or 0) for r in rows) if rows else 0
		self.total_weeks_in_ground = (self.weeks_to_pinch or 0) + last

		first = min((r.weeks_from_pinch or 0) for r in rows) if rows else 0
		self.first_harvest_offset_weeks = (
			(self.weeks_to_pinch or 0) + first + (self.calendar_rounding_weeks or 0)
		)

		# 4 x 13 = 52, so the block returns to the same weeks every year. This is the
		# size of the "flush family" a planting is locked into for its whole life.
		if self.flush_interval_weeks:
			self.harvest_weeks_per_year = round(WEEKS_PER_YEAR / self.flush_interval_weeks)
		else:
			self.harvest_weeks_per_year = 0

		years = (self.total_weeks_in_ground or 0) / WEEKS_PER_YEAR
		self.flushes_per_year = (self.total_flushes / years) if years else 0

		plants_per_ha = (self.plants_per_sqm_gross or 0) * 10_000
		self.stems_per_ha_life = (self.total_stems_per_plant_life or 0) * plants_per_ha
		self.stems_per_ha_year = (self.stems_per_ha_life / years) if years else 0

	# --------------------------------------------------------------- motherstock
	def set_motherstock(self):
		self.establishment_weeks = (
			(self.weeks_on_tray or 0)
			+ (self.weeks_on_pot or 0)
			+ (self.weeks_to_max_pc or 0)
			+ (self.hardening_weeks or 0)
		)
		self.plants_per_sqm_bench = (self.pots_per_sqm or 0) * (self.plants_per_pot or 0)

		cycles = self.max_multiplication_cycles or 0
		factor = self.multiplication_factor_per_cycle or 0
		self.max_multiplication_factor = 1 + (cycles * factor)
		self.lead_time_weeks = self.lead_time_for_cycles(cycles)

	def lead_time_for_cycles(self, cycles):
		"""On-farm weeks from receiving TC plantlets to a motherstock at max PC.

		With no build-up it is a single establishment. With build-up the multiplied
		generation needs its own establishment on top, so there is no cheap middle
		option between 0 and 1 cycle.
		"""
		est = self.establishment_weeks or 0
		if not cycles:
			return est
		return est + (cycles * (self.cycle_time_weeks or 0)) + est

	def tc_plants_for(self, mother_plants, cycles=None):
		"""TC plantlets needed to reach `mother_plants` after `cycles` of build-up."""
		if cycles is None:
			cycles = self.max_multiplication_cycles or 0
		factor = 1 + (cycles * (self.multiplication_factor_per_cycle or 0))
		return (mother_plants / factor) if factor else mother_plants

	# ---------------------------------------------------------- grades & losses
	def set_grades_and_losses(self):
		self.grade_total_pct = sum((r.allocation_pct or 0) for r in self.grade_allocation)
		if self.grade_allocation and abs(self.grade_total_pct - 100) > 0.01:
			frappe.msgprint(
				_("Grade allocation totals {0}%, not 100%.").format(self.grade_total_pct),
				indicator="orange",
				title=_("Check grade split"),
			)

		rooting = (self.rooting_success_pct or 100) / 100
		field = (self.field_establishment_pct or 100) / 100
		survival = rooting * field
		self.cuttings_per_plant_required = (1 / survival) if survival else 1

	# ------------------------------------------------------------------ helpers
	def flush_offsets(self):
		"""[(weeks after planting the harvest lands, stems per plant), ...]

		Includes the calendar rounding allowance, so offsets are directly usable
		against a Monday-based planning grid.
		"""
		rounding = self.calendar_rounding_weeks or 0
		return [
			(
				(self.weeks_to_pinch or 0) + (r.weeks_from_pinch or 0) + rounding,
				r.stems_per_plant or 0,
			)
			for r in sorted(self.flush_schedule, key=lambda r: r.flush_number or 0)
		]

	def stems_per_plant_at(self, weeks_after_planting):
		"""Stems per plant if a harvest lands this many weeks after planting."""
		for offset, stems in self.flush_offsets():
			if offset == weeks_after_planting:
				return stems
		return 0

	def beds_for_stems(self, stems, flush_index=1):
		"""Beds needed to deliver `stems` off a given flush (1-based)."""
		offsets = self.flush_offsets()
		if not offsets or flush_index > len(offsets):
			return 0
		stems_per_plant = offsets[flush_index - 1][1]
		if not stems_per_plant or not self.plants_per_bed:
			return 0
		return math.ceil((stems / stems_per_plant) / self.plants_per_bed)

	def harvest_week_family(self, planting_week):
		"""The weeks of the year this planting will harvest in, for its whole life.

		Because the flush interval divides the year, a planting returns to the same
		small set of weeks every year until it is uprooted.
		"""
		first = planting_week + (self.first_harvest_offset_weeks or 0)
		interval = self.flush_interval_weeks or 0
		if not interval:
			return []
		count = self.harvest_weeks_per_year or 0
		return sorted({((first + interval * k - 1) % WEEKS_PER_YEAR) + 1 for k in range(count)})
