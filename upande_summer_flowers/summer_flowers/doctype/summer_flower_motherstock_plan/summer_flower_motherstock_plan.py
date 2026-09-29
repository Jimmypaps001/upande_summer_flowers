# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""The motherstock line, decided and written down.

The procurement plan says what to buy. This says what happens after it arrives:
when each generation starts cutting, how many mothers are standing each week,
how that week's cut splits between going back as more mothers and going out to
the field, and whether the field got what it asked for.

It is meant to be played with. The two numbers at the top are the only decisions
-- plantlets to order, and how many weeks to divert the whole cut -- and Rebuild
works the other fifty-odd weeks out from them. Somebody who knows the market and
the plants can move either and watch what it does before committing to it.

The propagation plan is built from this rather than from the production plan,
because this is where the weekly supply of plants actually comes from.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


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
		r = ps.recommend(plan, tc=self.tc_to_order or None,
		                 divert_weeks=(self.divert_weeks
		                               if self.divert_weeks not in (None, "") else None))
		if not r.get("propagates"):
			frappe.throw(r.get("reason")
			             or _("{0} is not raised through a motherstock, so there is "
			                  "no line to plan.").format(self.variety))
		c = r.get("chosen")
		if not c:
			frappe.throw(r.get("reason")
			             or _("No order of plantlets covers this plan."))

		self.tc_to_order = cint(c["tc"])
		self.divert_weeks = cint(c["divert_weeks"])
		self.generations = len(c.get("generations") or [])
		self.cuttings_per_mother_per_week = flt(
			frappe.db.get_value("Crop Protocol Version", self.protocol,
			                    "cuttings_per_plant_per_week"))

		self.order_by_date = c["order_by"]
		self.tc_arrival_date = c["arrive_date"]
		self.first_cut_date = c["first_cut_date"]
		self.line_end_date = c["line_end_date"]
		self.peak_pool = cint(c["peak_pool"])
		self.plants_to_field = cint(c["total_to_field"])
		self.cuttings_required = cint((r.get("need") or {}).get("cuttings"))
		self.weeks_met = cint(c["weeks_met"])
		self.weeks_required = cint(c["weeks"])
		self.covers = 1 if c["covers"] else 0
		self.shortfall = cint(c["shortfall"])

		bits = [_("{0} plantlets, whole cut diverted back as mothers for {1} weeks "
		          "from the first cutting.").format("{:,}".format(self.tc_to_order),
		                                            self.divert_weeks)]
		bits.append(_("{0} generations; the block is cleared on {1}.")
		            .format(self.generations, self.line_end_date))
		if not self.covers:
			bits.append(_("Short by {0} cuttings across {1} weeks.")
			            .format("{:,}".format(self.shortfall),
			                    self.weeks_required - self.weeks_met))
		for a in r.get("assumed") or []:
			bits.append(a)
		self.notes = " ".join(bits)

		self.set("generation_table", [])
		for g in c.get("generations") or []:
			self.append("generation_table", g)
		self.set("schedule", [])
		for w in c.get("weeks_table") or []:
			self.append("schedule", w)
		return True


@frappe.whitelist()
def for_plan(production_plan, tc_to_order=None, divert_weeks=None):
	"""Make or refresh the motherstock plan behind a production plan.

	One per production plan: two of them would be two answers to a question that
	has one, and the propagation plan would not know which it was built from.
	"""
	existing = frappe.db.get_value("Summer Flower Motherstock Plan",
	                               {"production_plan": production_plan}, "name")
	doc = (frappe.get_doc("Summer Flower Motherstock Plan", existing) if existing
	       else frappe.new_doc("Summer Flower Motherstock Plan"))
	doc.production_plan = production_plan
	if tc_to_order not in (None, ""):
		doc.tc_to_order = cint(tc_to_order)
	if divert_weeks not in (None, ""):
		doc.divert_weeks = cint(divert_weeks)
	doc.rebuild()
	doc.save()
	return doc.name
