# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt

import math

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, flt, getdate, nowdate

from upande_summer_flowers.summer_flowers.planning import iso_year_week


class SummerFlowerMotherstockBatch(Document):
	def validate(self):
		self.protocol_doc = frappe.get_cached_doc("Crop Protocol Version", self.protocol)
		self.pull_peak_from_plan()
		self.size_requirement()
		self.size_tc_order()
		self.build_schedule()
		self.cost_it()
		self.build_steps()

	# ------------------------------------------------------------ requirement
	def pull_peak_from_plan(self):
		if self.production_plan and not self.peak_weekly_cuttings:
			plan = frappe.get_cached_doc("Summer Flower Production Plan", self.production_plan)
			self.peak_weekly_cuttings = plan.peak_weekly_sticking

	def size_requirement(self):
		p = self.protocol_doc
		spread = max(1, self.sticking_spread_weeks or 1)
		peak = (self.peak_weekly_cuttings or 0) / spread

		if self.apply_losses:
			peak *= p.cuttings_per_plant_required or 1

		self.effective_peak_cuttings = int(math.ceil(peak))

		# One cutting per mother per week means the mother count equals the peak
		# week's cutting requirement.
		per_plant_per_week = p.cuttings_per_plant_per_week or 1
		self.mother_plants = int(math.ceil(self.effective_peak_cuttings / per_plant_per_week))
		self.pots_required = int(math.ceil(self.mother_plants / (p.plants_per_pot or 1)))
		self.bench_sqm = (
			self.mother_plants / p.plants_per_sqm_bench if p.plants_per_sqm_bench else 0
		)
		# The outgoing and incoming generations overlap on the bench during renewal.
		self.peak_bench_sqm = self.bench_sqm * 2

	# --------------------------------------------------------------- TC order
	def size_tc_order(self):
		p = self.protocol_doc
		cycles = self.build_up_cycles or 0
		if cycles > (p.max_multiplication_cycles or 0):
			frappe.msgprint(
				_("Protocol {0} caps build-up at {1} cycles; you have set {2}.").format(
					p.name, p.max_multiplication_cycles, cycles
				),
				indicator="orange",
				title=_("Above protocol cap"),
			)

		self.multiplication_factor = 1 + (cycles * (p.multiplication_factor_per_cycle or 0))
		self.tc_plants_required = int(math.ceil(p.tc_plants_for(self.mother_plants, cycles)))
		self.lead_time_weeks = p.lead_time_for_cycles(cycles)
		self.total_lead_time_weeks = (self.lead_time_weeks or 0) + lab_turnaround_weeks()

	# --------------------------------------------------------------- schedule
	def build_schedule(self):
		p = self.protocol_doc
		stick = getdate(self.first_sticking_date)

		# The motherstock must be at max PC by the week the first cuttings are stuck.
		self.max_pc_date = stick
		self.tc_on_farm_date = add_days(stick, -7 * (self.lead_time_weeks or 0))
		self.tc_order_date = add_days(stick, -7 * (self.total_lead_time_weeks or 0))
		self.expiry_date = add_days(stick, 7 * (p.motherstock_life_weeks or 0))
		# Renewal must be productive the week this one expires, so the next order
		# goes in one full lead time before that.
		self.renewal_tc_order_date = add_days(
			self.expiry_date, -7 * (self.total_lead_time_weeks or 0)
		)

		warnings = []
		today = getdate(nowdate())
		if self.tc_order_date < today:
			warnings.append(
				_("The TC order date {0} has already passed — this schedule is not "
				  "achievable. Either move the first sticking week out, or cut build-up "
				  "cycles to shorten the lead time.").format(
					frappe.format(self.tc_order_date, {"fieldtype": "Date"})
				)
			)
		if self.renewal_tc_order_date < today:
			warnings.append(
				_("The renewal order for the next generation was already due on {0}.").format(
					frappe.format(self.renewal_tc_order_date, {"fieldtype": "Date"})
				)
			)
		if (self.lead_time_weeks or 0) > (p.motherstock_life_weeks or 0):
			warnings.append(
				_("Build-up takes {0} weeks but the motherstock only lives {1} weeks, so "
				  "two generations are always on the bench. Budget {2} m², not {3} m².").format(
					self.lead_time_weeks, p.motherstock_life_weeks,
					flt(self.peak_bench_sqm, 1), flt(self.bench_sqm, 1),
				)
			)
		self.schedule_warning = "\n\n".join(warnings) or None

	# ------------------------------------------------------------------- cost
	def cost_it(self):
		if not self.rate_per_plantlet:
			self.rate_per_plantlet = lookup_tc_rate(
				self.tc_plants_required, self.tc_stage, getdate(
					self.tc_order_date or nowdate()
				).year
			)

		self.tc_cost = flt(self.rate_per_plantlet) * (self.tc_plants_required or 0)

		settings = frappe.get_cached_doc("Summer Flower Settings")
		months = (self.protocol_doc.motherstock_life_weeks or 0) / 52 * 12
		self.holding_cost = (
			flt(settings.pot_holding_rate_per_month) * (self.pots_required or 0) * months
		)
		self.total_cost = flt(self.tc_cost) + flt(self.holding_cost)

	# --------------------------------------------------------------- build-up
	def build_steps(self):
		"""Lay out the build-up so the sequence is auditable, not just a lead time."""
		p = self.protocol_doc
		self.build_up_steps = []

		def step(label, date, plants, note=None):
			y, w = iso_year_week(date)
			self.append("build_up_steps", {
				"step": label,
				"week_label": f"{y}-W{w:02d}",
				"target_date": date,
				"mother_plants": int(plants),
				"pots": int(math.ceil(plants / (p.plants_per_pot or 1))),
				"notes": note,
			})

		tc_plants = self.tc_plants_required or 0
		cycles = self.build_up_cycles or 0
		est = p.establishment_weeks or 0

		step(_("Place TC order"), self.tc_order_date, tc_plants,
		     _("Includes {0} weeks lab turnaround.").format(lab_turnaround_weeks()))
		step(_("TC plantlets on farm"), self.tc_on_farm_date, tc_plants)

		cursor = add_days(self.tc_on_farm_date, 7 * est)
		step(_("TC generation productive"), cursor, tc_plants,
		     _("{0} weeks establishment: tray {1}, pot {2}, max PC {3}, hardening {4}.").format(
			     est, p.weeks_on_tray, p.weeks_on_pot, p.weeks_to_max_pc, p.hardening_weeks))

		plants = tc_plants
		for c in range(1, cycles + 1):
			cursor = add_days(cursor, 7 * (p.cycle_time_weeks or 0))
			plants = tc_plants * (1 + c * (p.multiplication_factor_per_cycle or 0))
			step(_("Multiplication cycle {0}").format(c), cursor, plants)

		if cycles:
			cursor = add_days(cursor, 7 * est)
			step(_("Multiplied generation productive"), cursor, plants,
			     _("Second establishment period — this is why build-up jumps from "
			       "{0} to {1} weeks at the first cycle.").format(est, est * 2 + 1))

		step(_("Cutting run starts"), self.max_pc_date, self.mother_plants,
		     _("{0} cuttings per week for {1} weeks.").format(
			     self.effective_peak_cuttings, p.motherstock_life_weeks))
		step(_("Order renewal TC"), self.renewal_tc_order_date, tc_plants,
		     _("Next generation must be productive the week this one expires."))
		step(_("Motherstock expires"), self.expiry_date, 0)


# ---------------------------------------------------------------------------

def lab_turnaround_weeks():
	return frappe.db.get_single_value("Summer Flower Settings", "lab_turnaround_weeks") or 0


def lookup_tc_rate(qty, stage="Stage 4", year=None):
	"""Rate per plantlet from the banded price list in Summer Flower Settings."""
	settings = frappe.get_cached_doc("Summer Flower Settings")
	if not settings.tc_price_bands or not qty:
		return 0

	years = {b.price_year for b in settings.tc_price_bands if b.price_year}
	if year not in years:
		year = max(years) if years else None

	field = "stage_3_rate" if stage == "Stage 3" else "stage_4_rate"
	for band in settings.tc_price_bands:
		if band.price_year != year:
			continue
		if (band.from_qty or 0) <= qty <= (band.to_qty or 0):
			return flt(band.get(field))
	return 0


@frappe.whitelist()
def cycle_options(protocol, mother_plants, stage="Stage 4", year=None):
	"""Cost and lead time for every build-up option, so the trade-off is visible.

	Band boundaries mean more cycles is not always cheaper: a smaller order can fall
	into a dearer band and cost more than a larger one.
	"""
	p = frappe.get_cached_doc("Crop Protocol Version", protocol)
	mother_plants = frappe.utils.cint(mother_plants)
	year = frappe.utils.cint(year) or getdate(nowdate()).year

	out = []
	for cycles in range(0, (p.max_multiplication_cycles or 0) + 1):
		tc = int(math.ceil(p.tc_plants_for(mother_plants, cycles)))
		rate = lookup_tc_rate(tc, stage, year)
		out.append({
			"cycles": cycles,
			"multiplication_factor": 1 + cycles * (p.multiplication_factor_per_cycle or 0),
			"tc_plants": tc,
			"rate": rate,
			"cost": flt(rate) * tc,
			"lead_time_weeks": p.lead_time_for_cycles(cycles),
			"total_lead_time_weeks": p.lead_time_for_cycles(cycles) + lab_turnaround_weeks(),
		})

	cheapest = min((o for o in out if o["cost"]), key=lambda o: o["cost"], default=None)
	for o in out:
		o["is_cheapest"] = bool(cheapest and o["cycles"] == cheapest["cycles"])
	return out
