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

from upande_summer_flowers.summer_flowers.planning import iso_monday


class SummerFlowerProcurementPlan(Document):
	def validate(self):
		self.set_totals()

	def on_submit(self):
		self.db_set("status", "Approved")
		# Not either/or. Aster is bought as tissue culture AND propagated here, and
		# the two used to exclude each other: choosing Purchase raised the orders and
		# never built the propagation plan, so the farm had plantlets coming and
		# nothing saying what to stick or when. The purchase is where the route
		# starts; propagation is every stage between there and the ground. A route
		# can want both, and this one does.
		if self.method == "Purchase":
			self.raise_material_requests()
		if cint(self.propagates_here):
			self.build_propagation_plan()
		# Whichever way the material was got, the farm needs to know what to plant
		# and when. How it was got only changes what the calendar records as its
		# source -- and which date it plants on.
		self.build_planting_plan()

	def raise_material_requests(self):
		"""One Material Request per supplier per week the material is needed.

		Not one per cohort: ten cohorts a week apart from one supplier are ten
		deliveries, not ten requests. They are left as drafts -- submitting one is
		a person committing money, and approving a plan is not that.
		"""
		from collections import defaultdict

		item = frappe.db.get_value("Item", self.variety, "name")
		if not item:
			frappe.msgprint(_("No Item called {0}, so nothing can be requested for it.")
			                .format(self.variety), indicator="orange")
			return

		groups = defaultdict(list)
		for r in self.requirements:
			if not cint(r.qty_to_order) or r.source_doc:
				continue
			when = getdate(r.required_at_site_date) if r.required_at_site_date else None
			groups[(r.supplier, when)].append(r)

		made, overdue, failed = [], [], []
		today = getdate(nowdate())
		for (supplier, when), rows in sorted(
			groups.items(), key=lambda kv: (kv[0][1] or getdate(nowdate()))
		):
			mr = frappe.new_doc("Material Request")
			mr.material_request_type = "Purchase"
			mr.company = self.company
			mr.transaction_date = today
			# A Material Request refuses a required-by date before its transaction
			# date, and the whole approval used to die on it -- so a plan whose first
			# order date had passed could not be approved at all, which is exactly the
			# plan someone most needs to approve and chase. The date it wanted is kept
			# on the requirement line; what goes on the request is the soonest it can
			# honestly be asked for, and the plan says which rows were already late.
			wanted = when or today
			if wanted < today:
				overdue.append((wanted, rows))
			mr.schedule_date = max(wanted, today)
			if mr.meta.get_field("custom_farm"):
				mr.custom_farm = self.farm
			for r in rows:
				mr.append("items", {
					"item_code": item,
					"qty": cint(r.qty_to_order),
					"schedule_date": mr.schedule_date,
					"warehouse": frappe.db.get_value("Warehouse",
					                                 {"company": self.company,
					                                  "is_group": 0}, "name"),
					"description": _("{0} as {1} for planting {2}").format(
						self.variety, self.entry_stage or _("plants"),
						r.planting_week or ""),
				})
			if supplier and mr.meta.get_field("supplier"):
				mr.supplier = supplier
			mr.flags.ignore_permissions = True
			# A site can make its own fields mandatory on a Material Request, and an
			# approval that dies on one of them takes the propagation plan and the
			# planting calendar down with it -- three things the farm needs, lost to
			# a field this module has never heard of. Approve, report, and let the
			# request be raised by hand.
			try:
				mr.insert()
			except Exception as e:
				frappe.log_error(frappe.get_traceback(),
				                 "Material Request for %s" % self.name)
				failed.append(str(e).split("\n")[0][:200])
				continue
			for r in rows:
				r.db_set("source_doctype", "Material Request", update_modified=False)
				r.db_set("source_doc", mr.name, update_modified=False)
			made.append(mr.name)

		if made:
			frappe.msgprint(_("{0} raised as drafts: {1}").format(
				len(made), ", ".join(made)), indicator="green",
				title=_("Material Requests"))
		if failed:
			frappe.msgprint(_(
				"{0} material request(s) could not be raised: {1}. The plan is still "
				"approved and its propagation plan and planting calendar are built; "
				"raise the order by hand, or fix what it objected to and approve an "
				"amendment."
			).format(len(failed), "; ".join(failed[:3])), indicator="red",
				title=_("Orders not raised"))
		if overdue:
			first = min(w for w, _r in overdue)
			frappe.msgprint(_(
				"{0} of these were needed on site before today -- the earliest was "
				"{1}. They are raised dated today, because a request cannot be "
				"wanted before it is made, but the material will arrive late and the "
				"plantings that depend on it will slip. The dates the plan asked for "
				"are still on the requirement lines."
			).format(len(overdue), first), indicator="orange",
				title=_("Ordered late"))
		else:
			frappe.msgprint(_("Nothing to order: every line is already raised, or "
			                  "has no quantity."), indicator="orange")

	def _propagation_coverage(self):
		"""{(year, week): (cuttings short, cuttings asked for)} for this season.

		Read off the propagation plan built moments ago in on_submit, which is the
		document that knows what the bench can cut. Empty when nothing is propagated
		here -- bought plants arrive or they do not, and that is the supplier's
		delivery date, not a cutting shortfall.
		"""
		if not cint(self.propagates_here):
			return {}
		name = frappe.db.get_value("Summer Flower Propagation Plan",
		                           {"production_plan": self.production_plan,
		                            "docstatus": ["<", 2]}, "name")
		if not name:
			return {}
		out = {}
		for w in frappe.get_all("Summer Flower Propagation Week",
		                        filters={"parent": name,
		                                 "parenttype": "Summer Flower Propagation Plan"},
		                        fields=["year", "week_no", "shortfall",
		                                "cuttings_required"]):
			if cint(w.shortfall) > 0:
				out[(cint(w.year), cint(w.week_no))] = (cint(w.shortfall),
				                                        cint(w.cuttings_required))
		return out


	def build_planting_plan(self):
		"""Turn the cohorts into Planting Calendar entries, whichever way they came.

		The calendar is what the farm works from, and it is the same document
		whether the plants were bought or raised here -- only seedling_source,
		and the supplier or batch beside it, differ.
		"""
		plan = frappe.get_doc("Summer Flower Production Plan", self.production_plan)
		# What the bench can actually deliver, week by week. The production plan says
		# what the market wants stuck; the propagation plan says what there are
		# cuttings for. Writing the first into the calendar as though it were the
		# second is how a farm comes to expect plants that were never raised.
		covered = self._propagation_coverage()
		# What the farm plants is what came off its own bench whenever anything is
		# raised here, even though the tissue culture behind it was bought. The
		# supplier delivered a plantlet, not a plant.
		source = ("In-house Propagation" if cint(self.propagates_here)
		          else "Purchased from Breeder")
		made, skipped, at_risk, refused = [], 0, [], []
		for r in self.requirements:
			if not cint(r.qty_at_field):
				continue
			row = next((b for b in plan.plan_blocks if b.name == r.cohort), None)
			if not row or not row.block:
				# No block yet. The calendar is a place as well as a date, so it
				# cannot be written until allocation has happened.
				skipped += 1
				continue
			if row.existing_planting and frappe.db.exists("Planting Calendar",
			                                              row.existing_planting):
				continue
			short, asked = covered.get((cint(row.sticking_year), cint(row.sticking_week)),
			                           (0, 0))
			shortfall_note = None
			if short and asked:
				shortfall_note = _(
					"The propagation plan is {0} cuttings short of the {1} this week "
					"needs, so this planting may go in smaller than {2} plants or "
					"later than {3}."
				).format(f"{short:,}", f"{asked:,}", f"{cint(r.qty_at_field):,}",
				         row.planting_date)
			cal = frappe.get_doc({
				"doctype": "Planting Calendar",
				"block": row.block,
				"farm": self.farm,
				"variety": self.variety,
				"crop_protocol_version": self.protocol,
				"company": self.company,
				"beds": cint(row.beds),
				"plants": cint(r.qty_at_field),
				# The delivery date is the planting date only where what arrives IS
				# the plant. Where the farm propagates, the delivery is tissue
				# culture months earlier, and planting on it put the crop in the
				# ground before it existed.
				"planting_date": (row.planting_date if cint(self.propagates_here)
				                  else (r.expected_delivery_date or row.planting_date)),
				"sticking_date": row.sticking_date if row.get("sticking_date") else None,
				"seedling_source": source,
				"supplier": (self.supplier if self.method == "Purchase"
				             and not cint(self.propagates_here) else None),
			})
			if shortfall_note:
				cal.notes = ((cal.get("notes") or "") + "\n" + shortfall_note).strip() \
					if cal.meta.has_field("notes") else cal.get("notes")
				at_risk.append(row.planting_date)
			cal.flags.ignore_permissions = True
			# One cohort the calendar refuses -- a protocol version that takes effect
			# after the planting date, a block rule this module knows nothing about --
			# must not cost the farm the other twenty-five and the propagation plan
			# built moments ago. Record which, and carry on.
			try:
				cal.insert()
			except Exception as e:
				frappe.log_error(frappe.get_traceback(),
				                 "Planting calendar for %s" % self.name)
				refused.append("%s: %s" % (row.planting_date,
				                           str(e).split("\n")[0][:160]))
				continue
			row.db_set("existing_planting", cal.name, update_modified=False)
			made.append(cal.name)

		if made:
			frappe.msgprint(_("{0} planting calendar entries created.").format(len(made)),
			                indicator="green", title=_("Planting plan"))
		if refused:
			frappe.msgprint(_(
				"{0} planting(s) were refused by the calendar and have no entry: {1}. "
				"Everything else is written, and the propagation plan is built. Fix "
				"what was objected to and approve an amendment for those cohorts."
			).format(len(refused), "; ".join(refused[:3])), indicator="red",
				title=_("Plantings not written"))
		if at_risk:
			frappe.msgprint(_(
				"{0} of these plantings fall in weeks the propagation plan cannot "
				"fully supply, the first on {1}. They are written to the calendar "
				"because that is the plan; what is short is on each entry and on "
				"the propagation plan's own weeks."
			).format(len(at_risk), min(at_risk)), indicator="orange",
				title=_("Not all of it can be stuck"))
		if skipped:
			frappe.msgprint(_("{0} cohorts have no block allocated, so no planting "
			                  "calendar entry could be written for them. Allocate "
			                  "blocks and approve again.").format(skipped),
			                indicator="orange", title=_("Planting plan"))

	def build_propagation_plan(self):
		"""Raise the propagation plan, now that propagating is what was chosen.

		This used to happen when the production plan was created, which answered the
		sourcing question before anyone had been asked it: every plan got a
		propagation plan whether or not the farm intended to propagate, and because
		there is one per variety per season, a second plan for the same crop quietly
		repointed the first one at itself.
		"""
		from upande_summer_flowers.summer_flowers.doctype \
			.summer_flower_propagation_plan.summer_flower_propagation_plan import (
				build_from_plan,
			)

		try:
			outcome = build_from_plan(self.production_plan, as_dict=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(),
			                 "Propagation plan for %s" % self.name)
			frappe.msgprint(_("The procurement plan is approved, but its propagation "
			                  "plan could not be built. The error is in the log."),
			                indicator="orange", title=_("Propagation"))
			return
		# A TC quantity or date confirmed on the dashboard is a decision, and this
		# document is built after it. Letting the new propagation plan recompute over
		# the top would quietly discard the choice at the one moment it starts to
		# matter -- the plan is raised, and it asks for a different order than the one
		# that was confirmed.
		carried = self._carry_tc_choice(outcome["name"])
		frappe.msgprint(_("{0} {1}.{2}").format(
			outcome["name"], _("created") if outcome.get("created") else _("updated"),
			" " + carried if carried else ""),
			indicator="green", title=_("Propagation plan"))

	def _carry_tc_choice(self, propagation_plan):
		"""Put the confirmed TC order onto the propagation plan just built."""
		p = frappe.db.get_value(
			"Summer Flower Production Plan", self.production_plan,
			["tc_choice_committed", "tc_plants_committed", "tc_order_date_committed"],
			as_dict=True)
		if not (p and cint(p.tc_choice_committed) and cint(p.tc_plants_committed)):
			return None
		d = frappe.get_doc("Summer Flower Propagation Plan", propagation_plan)
		if d.docstatus:
			return None
		d.tc_plants_required = cint(p.tc_plants_committed)
		if p.tc_order_date_committed:
			d.tc_order_date = getdate(p.tc_order_date_committed)
		d.flags.ignore_permissions = True
		d.save()
		return _("It carries the {0} plantlets confirmed on the dashboard, ordered "
		         "{1}.").format(f"{cint(p.tc_plants_committed):,}",
		                        p.tc_order_date_committed or _("(no date)"))

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	def set_totals(self):
		rows = self.requirements or []
		# The establishment line is the motherstock, not a planting: its qty_at_field
		# is mother plants standing on a bench, and adding it to the plants going in
		# the ground would overstate the crop by the size of the pool.
		field = [r for r in rows if (r.line_type or "Planting") != "Establishment"]
		self.total_plants_at_field = sum(cint(r.qty_at_field) for r in field)
		self.total_units_to_order = sum(cint(r.qty_to_order) for r in rows)
		self.beds_required = sum(cint(r.beds) for r in field)
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
	beds = cint(blocks.beds)
	if not beds:
		# Beds in a greenhouse that belongs to no block are not plantable ground
		# yet. Counting them would let an order be placed against land nobody has
		# laid out, and the plants would arrive with nowhere to go.
		loose = frappe.db.sql("""
			select count(*) n from tabBed b join tabWarehouse w on w.name = b.greenhouse
			where w.custom_farm = %s and ifnull(b.custom_block, '') = ''
		""", farm)[0][0]
		return None, (_("{0} has {1} beds belonging to no block. Draw blocks over "
		                "them before ordering against them.").format(farm, cint(loose))
		              if cint(loose) else
		              _("{0} has no summer flower blocks and no beds.").format(farm))
	return beds, _("{0} beds in summer flower blocks").format(beds)


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
	# A plan is pinned to the version it was built on, deliberately. But that means
	# a route added to the protocol since does not reach it, and the screen then
	# says the crop has no route when the protocol plainly does -- which reads as
	# the edit having been lost.
	current = frappe.db.get_value("Crop Protocol Version",
	                              {"variety": p.variety, "farm": p.farm,
	                               "is_current": 1}, "name")
	stale = None
	if current and current != v.name:
		newer_has_route = bool(frappe.db.count("Crop Material Stage",
		                                       {"parent": current,
		                                        "parenttype": "Crop Protocol Version"}))
		stale = {
			"current_version": current,
			"plan_version": v.name,
			"newer_has_route": newer_has_route,
		}
	# What the protocol already says. The dialog used to default to Purchase at
	# whichever stage happened to come first, which is not a default at all -- it is
	# a guess that looks like one, and on a crop the farm propagates it was wrong
	# in both fields at once.
	decided = sourcing.route_plan(v)
	# The protocol to send someone to when the route is what is missing. Followed
	# off the version's own link rather than rebuilt from variety and farm: a name
	# assembled from two fields is a guess, and a guess routes to a 404 the moment
	# a protocol is named anything else.
	crop_protocol = v.get("crop_protocol")
	if crop_protocol and not frappe.db.exists("Crop Protocol", crop_protocol):
		crop_protocol = None
	return {
		"plan": p.name, "variety": p.variety, "farm": p.farm,
		"protocol": v.name, "route": v.get("route_summary"),
		"crop_protocol": crop_protocol,
		# Where the route lives on that form, so the reader lands on the table they
		# were sent to fill in rather than at the top of a long protocol.
		"route_fieldname": "custom_sf_material_route",
		"decided": decided,
		"options": options,
		"beds_available": space, "space_basis": basis,
		"has_route": bool(v.material_route),
		"stale_version": stale,
	}


@frappe.whitelist()
def build(production_plan, method=None, entry_stage=None, supplier=None, fit_to_space=1):
	"""Turn a plan's plantings into one requirement line each, and price the order."""
	fit_to_space = cint(fit_to_space)
	p = frappe.get_doc("Summer Flower Production Plan", production_plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	from upande_summer_flowers.summer_flowers import sourcing
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_production_plan.summer_flower_production_plan import (
			plants_per_bed_for,
		)

	existing = frappe.db.get_value("Summer Flower Procurement Plan",
	                               {"production_plan": p.name, "docstatus": ["<", 2]},
	                               "name")
	if existing:
		frappe.throw(_("{0} already sources {1}. Cancel it, or amend it, rather than "
		               "raising a second set of orders for one plan.")
		             .format(existing, p.name))

	# The route decides how the material is got; a person only picks WHICH buyable
	# stage, and only when the protocol offers more than one. A choice that
	# contradicts the route is refused rather than silently obeyed -- a plan that
	# says Purchase on a crop taken off the farm's own stock orders from nobody.
	decided = sourcing.route_plan(v)
	if not decided["has_route"]:
		frappe.throw(decided["reason"], title=_("No route to buy along"))
	if decided.get("unmarked"):
		frappe.throw(decided["reason"], title=_("Nothing on the route is marked bought"))
	method = method or decided["method"]
	if method == "Purchase" and decided["method"] == "Propagate":
		frappe.throw(
			_("{0} takes its material off the farm's own crop, so there is nothing "
			  "to buy. {1}").format(v.name, decided["reason"]),
			title=_("Nothing to purchase"))
	if method == "Purchase":
		entry_stage = entry_stage or decided["entry_stage"]
		if entry_stage not in decided["buyable"]:
			frappe.throw(
				_("{0} is not a stage {1} is bought at. The protocol marks {2}.")
				.format(entry_stage, v.name,
				        ", ".join(decided["buyable"]) or _("none")),
				title=_("Not a buying stage"))
	else:
		entry_stage = None

	journey = sourcing.journey(v, entry_stage) if entry_stage else None
	per_unit = flt(journey["plants_per_unit"]) if journey else 1.0
	weeks_to_ground = cint(journey["weeks_to_ground"]) if journey else 0
	lead = cint(journey["lead_weeks"]) if journey else 0
	# A route that passes a motherstock is bought once, not once a cohort. The pool
	# is established and then cut from every week, so its size is set by the busiest
	# week's sticking and not by the season's total -- summing the cohorts bought
	# the same motherstock twenty-six times over.
	peak_monday = None
	if p.peak_sticking_week_planned:
		_y, _w = p.peak_sticking_week_planned.split("-W")
		peak_monday = iso_monday(cint(_y), cint(_w))
	need = (sourcing.requirement(v, entry_stage, cint(p.new_plants_required),
	                             cint(p.peak_weekly_sticking_planned),
	                             peak_date=peak_monday)
	        if entry_stage else None)
	standing = bool(need and need.get("kind") == "standing")
	if standing and need.get("blocked"):
		frappe.throw(need["blocked"], title=_("The order cannot be sized"))
	if need and not standing and not per_unit:
		frappe.throw(
			_("Nothing survives the route from {0} as the protocol has it, so no order "
			  "can be sized from it.").format(entry_stage),
			title=_("The order cannot be sized"))

	doc = frappe.new_doc("Summer Flower Procurement Plan")
	doc.production_plan = p.name
	doc.variety, doc.farm, doc.company = p.variety, p.farm, p.company
	doc.season, doc.protocol = p.get("season"), v.name
	doc.method = method
	doc.entry_stage = entry_stage if method == "Purchase" else None
	doc.supplier = supplier if method == "Purchase" else None
	doc.propagates_here = 1 if decided.get("propagates") else 0
	doc.in_house_stages = ", ".join(decided.get("in_house") or []) or None
	doc.route_verdict = decided.get("reason")
	doc.route_summary = v.get("route_summary")
	doc.weeks_to_ground, doc.lead_weeks = weeks_to_ground, lead
	doc.plants_per_unit = per_unit

	beds_available, basis = space_at(p.farm)
	if not beds_available:
		frappe.throw(_("{0} has no summer flower blocks, so there is nowhere to put "
		               "what you buy. Draw blocks over its beds first.").format(p.farm),
		             title=_("No land to plant"))
	doc.beds_available = beds_available
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
		# On a standing route the cohort buys nothing: it is stuck with cuttings off
		# the pool, and the pool was bought once on the establishment line below.
		units = 0 if standing else (
			int(math.ceil(plants / per_unit)) if (plants and per_unit) else 0)
		required = getdate(b.planting_date) if b.planting_date else None
		order_by = None if standing else (
			required - datetime.timedelta(weeks=weeks_to_ground + lead)
			if required else None)
		doc.append("requirements", {
			"line_type": "Planting",
			"drawn_from_pool": 1 if standing else 0,
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
			# What the plan is asking for. A supplier may come back with something
			# else, and that goes in confirmed_delivery_week -- the two are not the
			# same fact and the calendar keys on the second.
			"expected_delivery_date": required,
		})

	if standing:
		# One line for the one purchase, dated back through the function that knows
		# how long a pool takes to build. The plan's own TC block works the same date
		# out the same way, because it is the same function.
		from upande_summer_flowers.summer_flowers.doctype \
			.summer_flower_motherstock_batch.summer_flower_motherstock_batch import (
				tc_order_by_date,
			)

		stick = [(cint(b.sticking_year), cint(b.sticking_week)) for b in rows
		         if cint(b.sticking_year)]
		first = min(stick) if stick else None
		first_stick = iso_monday(first[0], first[1]) if first else None
		doc.append("requirements", {
			"line_type": "Establishment",
			"planting_week": ("%s-W%02d" % first if first else None),
			"planting_date": first_stick,
			"qty_at_field": cint(need.get("pool")),
			"beds": 0,
			"method": method,
			"entry_stage": doc.entry_stage,
			"supplier": doc.supplier,
			"qty_to_order": cint(need.get("units")),
			"required_at_site_date": first_stick,
			"order_by_date": tc_order_by_date(v, first_stick,
			                                  cycles=cint(need.get("cycles"))),
			"expected_delivery_date": first_stick,
			"notes": need.get("basis"),
		})
		doc.pool_plants = cint(need.get("pool"))
		doc.weekly_draw = cint(need.get("weekly_draw"))
		doc.sizing_basis = need.get("basis")

	wanted_beds = sum(cint(b.beds) for b in rows)
	if trimmed:
		short = wanted_beds - beds_available
		doc.space_note = _(
			"Not enough ground. The plan needs {0} beds and {1} has {2}, so it is "
			"{3} beds short and {4} of {5} plantings were trimmed. The demand is not "
			"reduced by this -- it still asks for {0} beds. Either find more land, "
			"or accept that this much of the season cannot be grown here."
		).format(wanted_beds, p.farm, beds_available, short, trimmed, len(rows))
	doc.insert(ignore_permissions=True)
	if doc.space_note:
		frappe.msgprint(doc.space_note, indicator="orange",
		                title=_("Not enough ground"))
	return doc.name
