# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt

import math

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt, getdate, nowdate

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
		self.describe_choice()

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

		self.multiplication_factor = p.multiplication_factor(cycles)
		self.lead_time_weeks = p.lead_time_for_cycles(cycles)
		self.total_lead_time_weeks = (self.lead_time_weeks or 0) + lab_lead_weeks(p)

		# The calculation always runs and is always shown, whether or not it is the
		# figure being ordered. A batch that quietly stopped telling you what the
		# requirement was would make an override impossible to judge.
		self.tc_plants_calculated = int(math.ceil(p.tc_plants_for(self.mother_plants, cycles)))
		if not (self.override_tc_plants and cint(self.tc_plants_required) > 0):
			self.tc_plants_required = self.tc_plants_calculated

	# ----------------------------------------------------------- the override
	def describe_choice(self):
		"""Say what an override buys, in the units the decision is made in.

		A quantity is hard to judge as a quantity: 8,556 against 6,845 means
		nothing until it is read back as mother plants, and a date means nothing
		until it is read as weeks earlier or later than the schedule wants. Both are
		stated, and shortfalls are stated as shortfalls rather than left to be
		inferred from two numbers sitting next to each other.
		"""
		p = self.protocol_doc
		lines = []

		if self.override_tc_plants:
			calc = cint(self.tc_plants_calculated)
			chosen = cint(self.tc_plants_required)
			gap = chosen - calc
			pool = int(round(p.mother_plants_for(chosen, self.build_up_cycles)))
			need = cint(self.mother_plants)
			lines.append(
				_("Ordering {0} plantlets where the requirement works out to {1}, "
				  "a difference of {2}. That order becomes about {3} mother plants "
				  "against the {4} this batch needs.").format(
					chosen, calc, ("+%d" % gap) if gap >= 0 else str(gap), pool, need))
			if pool < need:
				short = need - pool
				lines.append(
					_("That is {0} mother plants short, about {1}% of the pool. The "
					  "peak week will not be cut in full.").format(
						short, int(round(short * 100.0 / need)) if need else 0))

		if self.override_tc_order_date and self.tc_order_date_calculated:
			chosen_d = getdate(self.tc_order_date)
			calc_d = getdate(self.tc_order_date_calculated)
			weeks = int(round((chosen_d - calc_d).days / 7.0))
			if weeks:
				lines.append(
					_("Ordering {0}, which is {1} weeks {2} than the schedule wants. "
					  "The first sticking week moves with it, to {3}, and everything "
					  "hung off it -- planting, pinching, first harvest -- moves too.").format(
						frappe.format(chosen_d, {"fieldtype": "Date"}),
						abs(weeks), _("later") if weeks > 0 else _("earlier"),
						frappe.format(getdate(self.first_sticking_date),
						              {"fieldtype": "Date"})))

		self.tc_choice_note = "\n\n".join(lines) or None

	# --------------------------------------------------------------- schedule
	def build_schedule(self):
		p = self.protocol_doc

		# An overridden order date moves the first sticking week rather than sitting
		# next to it in contradiction. The two are one full lead time apart by
		# definition: order today, and the earliest the pool can be cut from is the
		# lead time away. Buying earlier means sticking earlier, and everything the
		# plan hangs off the sticking week -- planting, pinching, first harvest --
		# moves with it. Setting the date without moving the week would leave a batch
		# claiming an order date its own schedule disagrees with.
		# The week the schedule wants, held apart from the week the override forces.
		# Without this the baseline moves with the override -- the calculated order
		# date is derived from the sticking week, so shifting the week shifts the
		# baseline and the two always agree, leaving an override that reads as no
		# change at all. Captured before the shift, and only while it is still the
		# schedule's own answer.
		if not self.sticking_week_required or not self.override_tc_order_date:
			self.sticking_week_required = getdate(self.first_sticking_date)

		if self.override_tc_order_date and self.tc_order_date:
			implied = add_days(getdate(self.tc_order_date),
			                   7 * (self.total_lead_time_weeks or 0))
			if implied != getdate(self.first_sticking_date):
				self.first_sticking_date = implied

		stick = getdate(self.first_sticking_date)

		# The first cutting week and full capacity are not the same week. The pool
		# climbs the build-up while it is being cut, so max PC lands ramp_weeks - 1 weeks
		# after the first cut -- the last ramp step is the 100% one. Setting these
		# equal, as this did, is what made the build-up invisible and every plan read as
		# though a new pool delivered its full rate from day one.
		ramp = p.ramp_ratios()
		self.ramp_weeks = len(ramp)
		self.max_pc_date = add_days(stick, 7 * (len(ramp) - 1))
		self.tc_on_farm_date = add_days(stick, -7 * (self.lead_time_weeks or 0))
		self.tc_order_date_calculated = add_days(
			getdate(self.sticking_week_required), -7 * (self.total_lead_time_weeks or 0))
		if not (self.override_tc_order_date and self.tc_order_date):
			self.tc_order_date = self.tc_order_date_calculated
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
		if len(ramp) > 1:
			first_cap = int(round((self.mother_plants or 0)
			                      * (p.cuttings_per_plant_per_week or 1) * ramp[0]))
			warnings.append(
				_("{0} mother plants reach full capacity on {1}, not on {2}. The "
				  "first cutting week yields about {3} cuttings ({4}% of {5}), so "
				  "any requirement inside those {6} weeks is only partly covered.").format(
					self.mother_plants,
					frappe.format(self.max_pc_date, {"fieldtype": "Date"}),
					frappe.format(stick, {"fieldtype": "Date"}),
					first_cap, int(round(ramp[0] * 100)),
					self.effective_peak_cuttings, len(ramp),
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
		# The same definition the lead time uses, or the step dates drift away from
		# tc_on_farm_date + lead_time_weeks and the two disagree on the same batch.
		est = p.weeks_tc_to_first_cut()

		step(_("Place TC order"), self.tc_order_date, tc_plants,
		     _("Includes {0} weeks lab turnaround.").format(lab_lead_weeks(p)))
		step(_("TC plantlets on farm"), self.tc_on_farm_date, tc_plants)

		cursor = add_days(self.tc_on_farm_date, 7 * est)
		step(_("TC generation cuttable"), cursor, tc_plants,
		     _("{0} weeks to first cut: tray {1}, pot {2}. Full capacity a further "
		       "{3} weeks on.").format(est, p.weeks_on_tray, p.weeks_on_pot,
		                               max(0, (self.ramp_weeks or 1) - 1)))

		plants = tc_plants
		for c in range(1, cycles + 1):
			cursor = add_days(cursor, 7 * (p.cycle_time_weeks or 0))
			plants = tc_plants * p.multiplication_factor(c)
			step(_("Multiplication cycle {0}").format(c), cursor, plants)

		if cycles:
			cursor = add_days(cursor, 7 * est)
			step(_("Multiplied generation productive"), cursor, plants,
			     _("Second establishment period — this is why build-up jumps from "
			       "{0} to {1} weeks at the first cycle.").format(est, est * 2 + 1))

		step(_("First cutting, ramp begins"), self.first_sticking_date,
		     self.mother_plants,
		     _("{0}-week ramp to full capacity: {1}% of the full rate in week one.")
		     .format(self.ramp_weeks or 0, int(round((p.ramp_ratios() or [1])[0] * 100))))
		step(_("Full cutting capacity"), self.max_pc_date, self.mother_plants,
		     _("{0} cuttings per week for {1} weeks.").format(
			     self.effective_peak_cuttings, p.motherstock_life_weeks))
		step(_("Order renewal TC"), self.renewal_tc_order_date, tc_plants,
		     _("Next generation must be productive the week this one expires."))
		step(_("Motherstock expires"), self.expiry_date, 0)


# ---------------------------------------------------------------------------

def lab_lead_weeks(version):
	"""Order-to-delivery at the TC lab, from one place.

	The protocol carries supplier_lead_weeks per variety per farm and is versioned;
	Summer Flower Settings carries a single global figure. Both were in use -- the
	batch read the Setting, the lifecycle simulator read the protocol -- so the two
	engines dated the same order differently and their coverage numbers could not be
	reconciled. The protocol wins because it is the more specific and the auditable
	one; the Setting remains the fallback for a protocol that has not filled it in.
	"""
	# The protocol's figure, including zero. Zero is a real answer -- plantlets already
	# on the farm, or bought in rooted -- and treating it as "not filled in" meant the
	# journey said 0 weeks of supplier lead while every date built from this one
	# assumed the global 20, two numbers for one fact. The Setting is the fallback only
	# for a version that has no such field at all.
	weeks = getattr(version, "supplier_lead_weeks", None)
	if weeks is None:
		return lab_turnaround_weeks()
	return cint(weeks)


def tc_order_by_date(version, first_sticking_date, cycles=None):
	"""The last date a TC order can be placed and still reach a sticking week.

	Two waits, and only two: the lab's own order-to-delivery, then lead_time_weeks,
	which is already defined as arrival to first cutting including any multiplication
	cycles. Adding ms_establishment_weeks on top would count tray and pot twice.

	One function because there were two. The plan worked back by lab lead plus lead
	time and the dashboard by lead time alone, so the same plan showed "order by
	2025-11-03" on the document and "2026-08-10" on the dashboard -- nine months
	apart, for the single decision with the longest lead time in the process.

	`cycles` asks the question for a build-up other than the protocol's own, which
	is what makes cutting cycles worth trying: fewer cycles is a shorter lead time
	and therefore a later, easier order date, paid for in plantlets.
	"""
	if not first_sticking_date:
		return None
	lead = (cint(version.lead_time_for_cycles(cint(cycles)))
	        if cycles not in (None, "") else cint(version.lead_time_weeks))
	weeks = cint(lab_lead_weeks(version)) + lead
	return add_days(getdate(first_sticking_date), -7 * weeks)


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
			"multiplication_factor": p.multiplication_factor(cycles),
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
