# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""How a plan's plant material is got, and what that commits you to.

A production plan says what must stand in the ground and when. It does not say
where the plants come from, and that choice is not a detail: it sets the lead
time, the lead time sets the order date, and on a long route the order date is
most of a year before the planting. So it is chosen here, once, on its own
document, and the orders follow from it.

Two things this deliberately does NOT do. It does not change the demand -- what
the market wants is stated uncapped, and allocation deals with space afterwards.
And it does not raise anything until it is approved: a draft can be read, argued
with and thrown away without committing money.
"""

import datetime
import math

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate, nowdate


class SummerFlowerSourcingPlan(Document):
	def validate(self):
		self.set_totals()

	def on_submit(self):
		self.db_set("status", "Approved")

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	def set_totals(self):
		rows = self.requirements or []
		self.total_plants_at_field = sum(cint(r.qty_at_field) for r in rows)
		self.total_units_to_order = sum(cint(r.qty_to_order) for r in rows)
		self.beds_required = sum(cint(r.beds) for r in rows)
		dates = [getdate(r.order_by_date) for r in rows if r.order_by_date]
		self.first_order_by = min(dates) if dates else None
		self.last_order_by = max(dates) if dates else None
		today = getdate(nowdate())
		for r in rows:
			r.late = 1 if (r.order_by_date and getdate(r.order_by_date) < today) else 0
		self.orders_late = sum(1 for r in rows if r.late)
		self.capped_by_space = 1 if any(cint(r.capped) for r in rows) else 0


def space_at(farm):
	"""Beds that could take a planting at this farm, and where the count came from.

	Beds inside summer flower blocks, plus beds in the farm's greenhouses that
	belong to no block yet -- those are ground, even though nobody has drawn a
	block over them. A farm with neither returns None, which is not the same as
	zero: zero would trim every order to nothing and call it a plan.
	"""
	blocks = frappe.db.sql("""
		select ifnull(sum(custom_total_beds), 0) beds
		from tabBlock where farm = %s and custom_is_summer_flower_block = 1
	""", farm, as_dict=True)[0]
	loose = frappe.db.sql("""
		select count(*) n from tabBed b join tabWarehouse w on w.name = b.greenhouse
		where w.custom_farm = %s and ifnull(b.custom_block, '') = ''
	""", farm)[0][0]
	beds = cint(blocks.beds) + cint(loose)
	if not beds:
		return None, _("{0} has no summer flower blocks and no beds, so there is "
		               "nothing to measure the order against.").format(farm)
	parts = []
	if cint(blocks.beds):
		parts.append(_("{0} beds in summer flower blocks").format(cint(blocks.beds)))
	if cint(loose):
		parts.append(_("{0} beds in greenhouses belonging to no block").format(cint(loose)))
	return beds, " + ".join(parts)


@frappe.whitelist()
def methods_for(production_plan):
	"""The ways this plan's material could be got, for the button to offer.

	Every stage of the crop's route that could be bought, priced and dated, plus
	raising it here. A route that names no buyable stage still offers the stages it
	has -- a protocol nobody has finished filling in should not make the screen
	empty, it should make the screen say so.
	"""
	p = frappe.get_doc("Summer Flower Production Plan", production_plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	from upande_summer_flowers.summer_flowers import sourcing

	options, seen = [], set()
	for o in sourcing.entry_options(v.name):
		seen.add(o["stage"])
		options.append({
			"stage": o["stage"], "weeks_to_ground": o["weeks_to_ground"],
			"lead_weeks": o["lead_weeks"], "plants_per_unit": o["plants_per_unit"],
			"marked_buyable": 1,
		})
	for row in (v.material_route or []):
		if (row.row_type or "Stage") != "Stage" or row.stage in seen:
			continue
		j = sourcing.journey(v, row.stage)
		if not j:
			continue
		seen.add(row.stage)
		options.append({
			"stage": row.stage, "weeks_to_ground": j["weeks_to_ground"],
			"lead_weeks": j["lead_weeks"], "plants_per_unit": j["plants_per_unit"],
			"marked_buyable": 0,
		})
	space, basis = space_at(p.farm)
	return {
		"plan": p.name, "variety": p.variety, "farm": p.farm,
		"protocol": v.name, "route": v.get("route_summary"),
		"options": options,
		"beds_available": space, "space_basis": basis,
		"has_route": bool(v.material_route),
	}


@frappe.whitelist()
def build(production_plan, method, entry_stage=None, supplier=None, fit_to_space=1):
	"""Turn a plan's plantings into one requirement line each, and price the order."""
	fit_to_space = cint(fit_to_space)
	p = frappe.get_doc("Summer Flower Production Plan", production_plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	from upande_summer_flowers.summer_flowers import sourcing
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_production_plan.summer_flower_production_plan import (
			plants_per_bed_for,
		)

	existing = frappe.db.get_value("Summer Flower Sourcing Plan",
	                               {"production_plan": p.name, "docstatus": ["<", 2]},
	                               "name")
	if existing:
		frappe.throw(_("{0} already sources {1}. Cancel it, or amend it, rather than "
		               "raising a second set of orders for one plan.")
		             .format(existing, p.name))

	journey = sourcing.journey(v, entry_stage) if entry_stage else None
	per_unit = flt(journey["plants_per_unit"]) if journey else 1.0
	weeks_to_ground = cint(journey["weeks_to_ground"]) if journey else 0
	lead = cint(journey["lead_weeks"]) if journey else 0

	doc = frappe.new_doc("Summer Flower Sourcing Plan")
	doc.production_plan = p.name
	doc.variety, doc.farm, doc.company = p.variety, p.farm, p.company
	doc.season, doc.protocol = p.get("season"), v.name
	doc.method = method
	doc.entry_stage = entry_stage if method == "Purchase" else None
	doc.supplier = supplier if method == "Purchase" else None
	doc.route_summary = v.get("route_summary")
	doc.weeks_to_ground, doc.lead_weeks = weeks_to_ground, lead
	doc.plants_per_unit = per_unit

	beds_available, basis = space_at(p.farm)
	doc.beds_available = beds_available or 0
	doc.space_basis = basis

	ppb = plants_per_bed_for(v) or 0
	rows = [b for b in p.plan_blocks if b.is_new_planting]
	rows.sort(key=lambda b: (cint(b.planting_year), cint(b.planting_week)))

	beds_left = beds_available if (fit_to_space and beds_available) else None
	trimmed = 0
	for b in rows:
		beds = cint(b.beds)
		plants = cint(b.plants)
		capped = 0
		if beds_left is not None:
			if beds_left <= 0:
				capped, beds, plants = 1, 0, 0
			elif beds > beds_left:
				capped = 1
				beds = beds_left
				plants = beds * ppb if ppb else 0
			beds_left -= beds
		if capped:
			trimmed += 1
		units = int(math.ceil(plants / per_unit)) if (plants and per_unit) else 0
		required = getdate(b.planting_date) if b.planting_date else None
		order_by = (required - datetime.timedelta(weeks=weeks_to_ground + lead)
		            if required else None)
		doc.append("requirements", {
			"cohort": b.name,
			"planting_week": ("%s-W%02d" % (cint(b.planting_year), cint(b.planting_week))
			                  if b.planting_year else None),
			"planting_date": b.planting_date,
			"first_harvest_week": ("%s-W%02d" % (cint(b.first_harvest_year),
			                                     cint(b.first_harvest_week))
			                       if b.first_harvest_year else None),
			"qty_at_field": plants,
			"beds": beds,
			"capped": capped,
			"method": method,
			"entry_stage": doc.entry_stage,
			"supplier": doc.supplier,
			"qty_to_order": units,
			"required_at_site_date": required,
			"order_by_date": order_by,
		})

	if beds_left is None and fit_to_space:
		doc.space_note = basis
	elif trimmed:
		doc.space_note = _(
			"{0} of {1} plantings were trimmed to fit {2} beds. The demand is not "
			"reduced by this -- it still asks for {3} beds; this says what can be "
			"planted, not what is wanted."
		).format(trimmed, len(rows), beds_available, sum(cint(b.beds) for b in rows))
	doc.insert(ignore_permissions=True)
	return doc.name
