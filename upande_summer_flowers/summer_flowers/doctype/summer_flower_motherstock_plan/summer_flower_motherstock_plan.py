# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""The motherstock line, decided and written down.

The procurement plan says what to buy. This says what happens after it arrives:
when each generation starts cutting, how many mothers are standing each week,
how that week's cut splits between going back as more mothers and going out to
the field, and whether the field got what it asked for.

It is meant to be played with. The two numbers at the top are the only decisions
-- plantlets to order, and how many times to multiply -- and Rebuild works the
other fifty-odd weeks out from them. Somebody who knows the market and the
plants can move either and watch what it does before committing to it.

The order it sits in matters. The production plan says what the field wants; this
says what line of mothers would supply it and, out of that, how many plantlets to
buy; the procurement plan then orders THAT figure. Sizing used to happen inside
the procurement plan, which put the decision after the purchase order it was
supposed to justify.

The weekly split is demand-led. The field is served first, every week, from the
very first cut -- a ramping pool still roots and hardens, and those plants are
plantable -- and only what the field does not ask for can go back as mothers.
The farm sends back four times at the most.

The propagation plan is built from this rather than from the production plan,
because this is where the weekly supply of plants actually comes from.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt, getdate


class SummerFlowerMotherstockPlan(Document):
	def validate(self):
		self.pull_from_plan()
		if not self.get("schedule"):
			self.rebuild()

	def pull_from_plan(self):
		if not self.production_plan:
			return
		p = frappe.get_cached_doc("Summer Flower Production Plan", self.production_plan)
		self.variety = p.variety
		self.farm = p.farm
		self.protocol = p.protocol
		self.season = p.get("season")
		if not self.company:
			self.company = p.get("company")

	@frappe.whitelist()
	def rebuild(self):
		"""Work the whole line out again from the two numbers at the top."""
		from upande_summer_flowers.summer_flowers import propagation_solver as ps

		if not self.production_plan:
			frappe.throw(_("Pick a production plan first."))
		self.pull_from_plan()
		plan = frappe.get_doc("Summer Flower Production Plan", self.production_plan)

		sizing = ps.peak_sizing(
			plan, cycles=(self.multiplications
			              if self.multiplications not in (None, "") else None))
		if not sizing:
			frappe.throw(_("This plan has no new plantings, so there is no peak "
			               "week to size a motherstock line from."))
		sch = ps.peak_schedule(plan, sizing,
		                       cycles=(self.multiplications
		                               if self.multiplications not in (None, "")
		                               else None),
		                       tc=self.tc_to_order or None)
		if not sch:
			frappe.throw(_("No order of plantlets covers this plan."))

		self.tc_to_order = cint(sch["tc"])
		self.multiplications = cint(sch["cycles"])
		self.generations = len(sch.get("generations") or [])
		self.cuttings_per_mother_per_week = flt(
			sizing["cuttings_per_mother_per_week"])

		self.order_by_date = sch["order_by"]
		# The plantlets land one establishment before the first cut, not on the
		# order date -- that was reading the order date back out of the option.
		self.tc_arrival_date = add_days(getdate(sch["first_cut_date"]),
		                                -7 * cint(sizing["establishment_weeks"]))
		self.first_cut_date = sch["first_cut_date"]
		self.line_end_date = sch["line_end_date"]
		self.peak_pool = cint(sch["peak_pool"])
		self.plants_to_field = cint(sch["to_field"])
		self.cuttings_required = cint(sizing["season_cuttings"])
		self.weeks_met = cint(sch["weeks_met"])
		self.weeks_required = cint(sch["weeks"])
		self.covers = 1 if sch["covers"] else 0
		self.shortfall = cint(sch["shortfall"])

		bits = [_("Peak week {0} wants {1} cuttings. Across {2} generations that "
		          "is {3} plantlets, rounded up to a whole planting area.").format(
			sizing["peak_week_label"], "{:,}".format(cint(sizing["peak_cuttings"])),
			self.generations, "{:,}".format(self.tc_to_order))]
		if cint(sch["sends_made"]):
			bits.append(_("Sent back to multiplication {0} time(s) out of {1} "
			              "allowed; the field is served first every week, so only "
			              "the surplus goes back.").format(
				cint(sch["sends_made"]), cint(sch["sends_allowed"])))
		else:
			bits.append(_("Nothing is sent back: the field asks for the whole cut, "
			              "so the pool has to be bought outright."))
		bits.append(_("Order by {0}; first cut {1}; the block is cleared on {2}.")
		            .format(self.order_by_date, self.first_cut_date,
		                    self.line_end_date))
		if not self.covers:
			bits.append(_("Short by {0} cuttings across {1} weeks.")
			            .format("{:,}".format(self.shortfall),
			                    self.weeks_required - self.weeks_met))
		for a in ps._assumptions(
				frappe.get_cached_doc("Crop Protocol Version", self.protocol)):
			bits.append(a)
		self.notes = " ".join(bits)

		self.set("generation_table", [])
		for g in sch.get("generations") or []:
			self.append("generation_table", g)
		self.set("schedule", [])
		for w in sch.get("weeks_table") or []:
			self.append("schedule", w)
		return True


@frappe.whitelist()
def for_plan(production_plan, tc_to_order=None, multiplications=None,
             divert_weeks=None):
	"""Make or refresh the motherstock plan behind a production plan.

	One per production plan: two of them would be two answers to a question that
	has one, and neither the propagation plan nor the procurement plan would know
	which it was built from.

	``divert_weeks`` is accepted and ignored so older callers and saved links do
	not break on it.
	"""
	existing = frappe.db.get_value("Summer Flower Motherstock Plan",
	                               {"production_plan": production_plan}, "name")
	doc = (frappe.get_doc("Summer Flower Motherstock Plan", existing) if existing
	       else frappe.new_doc("Summer Flower Motherstock Plan"))
	doc.production_plan = production_plan
	if tc_to_order not in (None, ""):
		doc.tc_to_order = cint(tc_to_order)
	if multiplications not in (None, ""):
		doc.multiplications = cint(multiplications)
	doc.rebuild()
	doc.save()
	return doc.name
