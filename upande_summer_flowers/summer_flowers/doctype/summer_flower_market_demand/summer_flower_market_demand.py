# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from upande_summer_flowers.summer_flowers.planning import (
	current_week,
	iso_monday,
	iso_year_week,
	month_of,
	validate_week,
	week_sequence,
	weeks_between,
)


class SummerFlowerMarketDemand(Document):
	def validate(self):
		self.check_one_per_variety()
		self.normalise_weeks()
		self.set_horizon()
		self.set_totals()
		self.set_grade_total()

	# ------------------------------------------------------------------- weeks
	def normalise_weeks(self):
		"""Sort chronologically, fill dates and month names, reject duplicates."""
		seen = set()
		for row in self.demand_weeks:
			validate_week(row.week_no, _("Week in row {0}").format(row.idx))
			key = (row.year, row.week_no)
			if key in seen:
				frappe.throw(
					_("Week {0} of {1} appears more than once in the demand table.").format(
						row.week_no, row.year
					)
				)
			seen.add(key)
			row.week_start_date = iso_monday(row.year, row.week_no)
			row.month_name = month_of(row.year, row.week_no)[1]

		self.demand_weeks = sorted(
			self.demand_weeks, key=lambda r: (r.year or 0, r.week_no or 0)
		)
		for idx, row in enumerate(self.demand_weeks, start=1):
			row.idx = idx

	def check_one_per_variety(self):
		"""One register per variety at a farm, and only one.

		The register is the statement of what the market wants for this crop here.
		A second one for the same crop makes every downstream question ambiguous --
		which demand did this plan come from, which is the demand -- and nothing
		reads more than one of them anyway. Extend the horizon or edit the weeks
		instead of starting another.
		"""
		if not (self.variety and self.farm):
			return
		# Not "name != self.name": autoname runs before validate, so a new document
		# already carries the name it is about to collide with and would exclude the
		# very record it duplicates -- leaving a raw "Duplicate entry" from MySQL
		# instead of an explanation.
		filters = {"variety": self.variety, "farm": self.farm}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		dupe = frappe.db.get_value("Summer Flower Market Demand", filters, "name")
		if dupe:
			frappe.throw(_(
				"{0} is already the demand register for {1} at {2}. Edit it or "
				"extend its horizon rather than creating a second one -- a variety "
				"has one demand."
			).format(dupe, self.variety, self.farm), title=_("Register exists"))

	def set_horizon(self):
		rows = self.demand_weeks
		self.weeks_covered = len(rows)

		if not rows:
			self.horizon_start = self.horizon_end = None
			self.weeks_ahead_of_today = 0
			self.horizon_gap_weeks = (self.target_years_ahead or 0) * 52
			self.horizon_status = _("Empty")
			return

		first, last = rows[0], rows[-1]
		self.horizon_start = f"{first.year}-W{first.week_no:02d}"
		self.horizon_end = f"{last.year}-W{last.week_no:02d}"

		this_year, this_week = current_week()
		self.weeks_ahead_of_today = weeks_between(
			this_year, this_week, last.year, last.week_no
		)

		target_weeks = self.target_weeks_ahead()
		self.horizon_gap_weeks = max(0, target_weeks - (self.weeks_ahead_of_today or 0))
		if self.horizon_gap_weeks:
			self.horizon_status = _("Short by {0} weeks").format(self.horizon_gap_weeks)
		else:
			self.horizon_status = _("{0} weeks ahead").format(self.weeks_ahead_of_today)

	def target_weeks_ahead(self):
		"""Weeks from today to the target horizon, measured on the real calendar."""
		years = self.target_years_ahead or 0
		if not years:
			return 0
		this_year, this_week = current_week()
		today_monday = iso_monday(this_year, this_week)
		try:
			target = today_monday.replace(year=today_monday.year + years)
		except ValueError:  # 29 Feb
			target = today_monday.replace(year=today_monday.year + years, day=28)
		ty, tw = iso_year_week(target)
		return weeks_between(this_year, this_week, ty, tw)

	def set_totals(self):
		rows = self.demand_weeks
		self.total_demand_stems = sum((r.demand_stems or 0) for r in rows)
		self.firm_demand_stems = sum((r.demand_stems or 0) for r in rows if r.is_firm)
		self.peak_weekly_demand = max((r.demand_stems or 0) for r in rows) if rows else 0
		self.average_weekly_demand = (self.total_demand_stems / len(rows)) if rows else 0

	def set_grade_total(self):
		self.grade_total_pct = sum((r.allocation_pct or 0) for r in self.demand_grades)

	# ----------------------------------------------------------------- extend
	@frappe.whitelist()
	def extend_horizon(self, weeks=None, copy_from_last_year=1):
		"""Append weeks so the register reaches the target years ahead.

		Demand is entered progressively, so this only ever adds empty (or
		last-year-seeded) weeks to the end -- it never touches existing rows.
		"""
		copy_from_last_year = frappe.utils.cint(copy_from_last_year)
		weeks = frappe.utils.cint(weeks) or self.horizon_gap_weeks
		if not weeks:
			return {"added": 0, "message": _("Horizon already reaches the target.")}

		if self.demand_weeks:
			last = self.demand_weeks[-1]
			start_year, start_week = iso_year_week(
				iso_monday(last.year, last.week_no) + frappe.utils.datetime.timedelta(weeks=1)
			)
		else:
			start_year, start_week = current_week()

		# Seed each new week from the same week one year earlier, so a progressive
		# horizon starts from last year's shape rather than zero.
		prior = {(r.year, r.week_no): r.demand_stems for r in self.demand_weeks}

		added = 0
		for year, week, monday in week_sequence(start_year, start_week, weeks):
			seed = 0
			if copy_from_last_year:
				seed = prior.get((year - 1, week)) or 0
			self.append("demand_weeks", {
				"year": year,
				"week_no": week,
				"week_start_date": monday,
				"demand_stems": seed,
				"month_name": month_of(year, week)[1],
				"is_firm": 0,
			})
			added += 1

		self.save()
		return {
			"added": added,
			"message": _("Added {0} weeks. Horizon now ends {1}.").format(
				added, self.horizon_end
			),
		}

	# ------------------------------------------------------------------- plan
	@frappe.whitelist()
	def create_production_plan(self, from_year=None, from_week=None, weeks=None):
		"""Build a Production Plan covering part or all of this demand register."""
		from upande_summer_flowers.summer_flowers.doctype.summer_flower_production_plan.summer_flower_production_plan import (
			build_from_demand,
		)

		return build_from_demand(self.name, from_year, from_week, weeks)
