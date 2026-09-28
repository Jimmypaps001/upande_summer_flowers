# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""The season's plantings on one page, for the people who have to do them.

There is one Planting Calendar per cohort, and there has to be: bed allocation,
the stock issue, the crop cycle and block occupancy all hang off it. But
thirty-eight records is not a plan anybody can be handed. A manager asks three
things about a season -- when do the plants arrive, when do we plant them, when
does the first cut come back -- and the answers were spread across thirty-eight
documents and two others.

So this reads them. It stores nothing it could look up: every row is built from
the Planting Calendar it names, and rebuilding is the only way to change it. A
figure here can be wrong only by being stale, never by disagreeing.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate


class SummerFlowerSeasonPlan(Document):
	def validate(self):
		self.rebuild()

	@frappe.whitelist()
	def rebuild(self):
		"""Read the season's plantings back off the calendars."""
		cals = frappe.get_all(
			"Planting Calendar",
			filters={"variety": self.variety, "farm": self.farm,
			         "calendar_status": ["!=", "Cancelled"]},
			fields=["name", "block", "beds", "plants", "planting_date",
			        "actual_planting_date", "harvest_week_family",
			        "calendar_status", "seedling_source", "supplier",
			        "first_receipt_date", "planting_year", "planting_week",
			        "stock_entry", "dispatch_entry"],
			order_by="planting_date asc")
		if self.season_start_year:
			cals = [c for c in cals if self._in_season(c)]

		deliver = self._delivery_dates()
		self.set("rows", [])
		for c in cals:
			arrive = (c.first_receipt_date
			          or deliver.get((cint(c.planting_year), cint(c.planting_week)))
			          or c.planting_date)
			self.append("rows", {
				"planting": c.name,
				"block": c.block,
				"beds": cint(c.beds),
				"plants": cint(c.plants),
				"bed_numbers": self._bed_numbers(c.name),
				"plants_arrive": arrive,
				"plant_on": c.actual_planting_date or c.planting_date,
				"first_cut": self._first_cut(c),
				"status": c.calendar_status,
				"note": self._note(c),
			})
		self._roll_up()

	# ------------------------------------------------------------------ pieces
	def _in_season(self, cal):
		"""A season runs 1 July to 30 June, the same span the plan uses."""
		d = getdate(cal.actual_planting_date or cal.planting_date)
		if not d:
			return False
		start = cint(self.season_start_year)
		return getdate("%s-07-01" % start) <= d <= getdate("%s-06-30" % (start + 1))

	def _delivery_dates(self):
		"""When the propagation unit hands each planting week its plants.

		Only for a crop raised here; one bought in arrives on its supplier's date,
		which the calendar already records as first_receipt_date.
		"""
		name = frappe.db.get_value(
			"Summer Flower Propagation Plan",
			{"variety": self.variety, "season_start_year": self.season_start_year,
			 "docstatus": ["<", 2]}, "name")
		if not name:
			return {}
		out = {}
		for w in frappe.get_all(
				"Summer Flower Propagation Week",
				filters={"parent": name,
				         "parenttype": "Summer Flower Propagation Plan"},
				fields=["deliver_on", "plant_week"]):
			for token in (w.plant_week or "").split(","):
				token = token.strip()
				if "-W" not in token:
					continue
				y, wk = token.split("-W")
				out[(cint(y), cint(wk))] = w.deliver_on
		return out

	def _bed_numbers(self, planting):
		rows = frappe.get_all("Planting Calendar Bed",
		                      filters={"parent": planting,
		                               "parenttype": "Planting Calendar"},
		                      fields=["bed_number"], order_by="bed_number asc")
		nums = [cint(r.bed_number) for r in rows if r.bed_number]
		if not nums:
			return None
		# Runs rather than a list of forty numbers: "1-24, 31-36" is what a manager
		# writes on a board.
		runs, start, prev = [], nums[0], nums[0]
		for n in nums[1:]:
			if n == prev + 1:
				prev = n
				continue
			runs.append((start, prev))
			start = prev = n
		runs.append((start, prev))
		return ", ".join(str(a) if a == b else "%s-%s" % (a, b) for a, b in runs)

	def _first_cut(self, cal):
		"""The week the first flush lands, with its year.

		Off the flush projection, which is dated. harvest_week_family is a
		week-OF-YEAR label -- "wk1, wk14" -- describing which weeks of any year the
		crop flushes in, and reading the first token off it put "wk8" in a column a
		manager reads as a date.
		"""
		row = frappe.db.get_value("Planting Calendar Flush",
		                          {"parent": cal.name, "flush_number": 1},
		                          ["year", "week_no", "harvest_date"], as_dict=True)
		if row and row.year and row.week_no:
			return "%s-W%02d" % (cint(row.year), cint(row.week_no))
		return None

	def _note(self, cal):
		bits = []
		if not cal.block:
			bits.append(_("no block yet"))
		if cal.actual_planting_date:
			bits.append(_("planted"))
		if cal.seedling_source == "Purchased from Breeder" and cal.supplier:
			bits.append(_("from {0}").format(cal.supplier))
		if cal.dispatch_entry:
			bits.append(_("moved from propagation"))
		return " · ".join(bits) or None

	def _roll_up(self):
		rows = self.rows or []
		self.plantings = len(rows)
		self.beds = sum(cint(r.beds) for r in rows)
		self.plants = sum(cint(r.plants) for r in rows)
		self.blocks_used = len({r.block for r in rows if r.block})
		self.planted_so_far = sum(1 for r in rows if (r.status or "") == "Planted")
		arrivals = [getdate(r.plants_arrive) for r in rows if r.plants_arrive]
		plantings = [getdate(r.plant_on) for r in rows if r.plant_on]
		cuts = sorted({r.first_cut for r in rows if r.first_cut})
		self.first_arrival = min(arrivals) if arrivals else None
		self.first_planting = min(plantings) if plantings else None
		self.last_planting = max(plantings) if plantings else None
		self.first_cut = cuts[0] if cuts else None
		self.last_cut = cuts[-1] if cuts else None
		if not rows:
			self.summary = _("No plantings for {0} at {1} in this season yet. They "
			                 "are written when a procurement plan is approved, one "
			                 "per cohort that has a block."
			                 ).format(self.variety, self.farm)
			return
		self.summary = _(
			"{0} plantings, {1} beds, {2} plants across {3} blocks. Plants start "
			"arriving {4}, planting runs {5} to {6}, and the first cut is {7}."
		).format(self.plantings, self.beds, "{:,}".format(cint(self.plants)),
		         self.blocks_used,
		         frappe.format(self.first_arrival, {"fieldtype": "Date"}),
		         frappe.format(self.first_planting, {"fieldtype": "Date"}),
		         frappe.format(self.last_planting, {"fieldtype": "Date"}),
		         self.first_cut)


@frappe.whitelist()
def for_season(variety, farm, season_start_year, production_plan=None):
	"""The season plan for this crop, made if it is not there and brought up to date."""
	name = frappe.db.get_value("Summer Flower Season Plan",
	                           {"variety": variety, "farm": farm,
	                            "season_start_year": cint(season_start_year)},
	                           "name")
	if name:
		doc = frappe.get_doc("Summer Flower Season Plan", name)
		if production_plan:
			doc.production_plan = production_plan
	else:
		doc = frappe.new_doc("Summer Flower Season Plan")
		doc.variety, doc.farm = variety, farm
		doc.season_start_year = cint(season_start_year)
		doc.season = "%s-%s" % (cint(season_start_year),
		                        str(cint(season_start_year) + 1)[-2:])
		doc.production_plan = production_plan
		doc.company = frappe.db.get_value("Summer Flower Production Plan",
		                                  production_plan, "company") \
			if production_plan else None
	doc.flags.ignore_permissions = True
	doc.save()
	return doc.name
