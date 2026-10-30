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
from frappe.utils import add_days, cint, flt, getdate, nowdate

from upande_summer_flowers.summer_flowers.planning import iso_monday


class SummerFlowerProcurementPlan(Document):
	def validate(self):
		self.set_totals()
		self.set_purchase_amounts()
		self.set_allocation_amounts()
		self.check_sourcing()
		self.check_purchase()
		self.check_allocation()

	def propagates(self):
		"""Whether this plan's material has to be raised before it can be planted.

		A route that buys finished plants has nobody to allocate to. Anything
		bought earlier than that -- tissue culture, roots, seed, cuttings -- has
		weeks between the purchase and a plant, and somebody has to do them.
		"""
		return bool(cint(self.propagates_here) or (self.in_house_stages or "").strip()
		            or self.get("propagation_allocation"))

	def units_by_stage(self):
		"""What each way in has to buy, from the lines that buy it."""
		out = {}
		for r in (self.requirements or []):
			if not cint(r.qty_to_order):
				continue
			stage = r.entry_stage or self.entry_stage or ""
			out[stage] = out.get(stage, 0) + cint(r.qty_to_order)
		return out

	def plants_by_stage(self):
		"""What each way in has to become, in plants standing."""
		out = {}
		for r in (self.requirements or []):
			if (r.line_type or "Planting") != "Planting":
				continue
			stage = r.entry_stage or self.entry_stage or ""
			out[stage] = out.get(stage, 0) + cint(r.qty_at_field)
		if not out:
			out[self.main_entry_stage() or ""] = cint(self.total_plants_at_field)
		return out

	def weeks_to_raise(self, stage):
		"""What the protocol says raising THIS material takes.

		Not the plan's one figure. A crop bought as tissue culture and as roots
		has two journeys to the ground and they are nothing like the same length,
		so one number against both says the roots are ready months after they are.
		"""
		if not stage or not self.protocol:
			return cint(self.weeks_to_ground)
		try:
			from upande_summer_flowers.summer_flowers import sourcing

			j = sourcing.journey(self.protocol, stage)
			return cint(j["weeks_to_ground"]) if j else cint(self.weeks_to_ground)
		except Exception:
			return cint(self.weeks_to_ground)

	def set_purchase_amounts(self):
		"""Who sells each way in, and what that commits.

		Buying and raising are two decisions and the document carried only one of
		them: a single supplier field, so a crop bought as tissue culture from two
		laboratories had nowhere to say so, and a crop bought AND propagated could
		allocate the propagating and not the buying.

		A share here is a share of ITS OWN way in, not of the whole order. On a
		crop bought 82% as tissue culture and 18% as roots, "60%" against a lab
		means 60% of the tissue culture -- which is what anybody ordering would
		mean by it.
		"""
		totals = self.units_by_stage()
		first_need = {}
		for r in (self.requirements or []):
			if not r.required_at_site_date:
				continue
			stage = r.entry_stage or self.entry_stage or ""
			d = getdate(r.required_at_site_date)
			if stage not in first_need or d < first_need[stage]:
				first_need[stage] = d
		only = next(iter(totals), None) if len(totals) == 1 else None

		for row in self.get("purchase_allocation") or []:
			if not row.entry_stage:
				row.entry_stage = only or self.main_entry_stage()
			if row.supplier and not cint(row.lead_weeks):
				row.lead_weeks = cint(frappe.db.get_value(
					"Supplier", row.supplier, "custom_sf_propagation_weeks"))
			if not cint(row.lead_weeks):
				row.lead_weeks = cint(self.lead_weeks)
			total = cint(totals.get(row.entry_stage, 0))
			row.units = int(round(total * flt(row.share_pct) / 100.0))
			row.amount = cint(row.units) * flt(row.rate)
			arrives = first_need.get(row.entry_stage)
			row.arrives = arrives
			row.order_by = (add_days(arrives, -7 * cint(row.lead_weeks))
			                if arrives else None)

		# The header's supplier is the table's answer, not a second question. It
		# was asked twice -- pick a seller in the grid and the field above it was
		# still empty, and the plan still said nothing named where this is bought.
		named = [r.supplier for r in (self.get("purchase_allocation") or [])
		         if r.supplier]
		if named:
			self.supplier = named[0] if len(set(named)) == 1 else None

		# And the lines the orders are grouped on. They were written with
		# whatever the header said when the plan was built, so clearing the
		# table left a stale name behind and the plan went on believing it knew
		# where this is bought.
		if self.get("purchase_allocation"):
			sole = {}
			for row in self.purchase_allocation:
				stage = row.entry_stage or ""
				if stage not in sole:
					sole[stage] = row.supplier
				elif sole[stage] != row.supplier:
					sole[stage] = None
			for r in (self.requirements or []):
				stage = r.entry_stage or self.entry_stage or ""
				if stage in sole:
					r.supplier = sole[stage]
		elif not self.supplier:
			for r in (self.requirements or []):
				r.supplier = None

	def check_purchase(self):
		"""Every way in has to be bought from somebody, and bought once.

		Only checked where there is something to buy: a route taking its material
		off the farm's own crop has no sellers and wants none.
		"""
		rows = self.get("purchase_allocation") or []
		if self.method != "Purchase" or not rows:
			return
		# A blank line is what "Choose Suppliers" hands you to fill in, so it
		# cannot be refused on the save that creates it. It is refused at
		# approval, where an order with no seller is a real problem.
		blank = [r.idx for r in rows if not r.supplier]
		if blank:
			msg = _("Row(s) {0} have no supplier yet.").format(
				", ".join(str(i) for i in blank))
			if self.docstatus == 1 or self.flags.in_submit:
				frappe.throw(msg, title=_("Nobody is selling this"))
			frappe.msgprint(msg, indicator="orange", alert=True)
		by_stage = {}
		for r in rows:
			if not r.supplier:
				continue
			by_stage.setdefault(r.entry_stage or "", 0)
			by_stage[r.entry_stage or ""] += flt(r.share_pct)
		for stage, share in by_stage.items():
			if round(share, 2) > 100.5:
				frappe.throw(_("The sellers of {0} add up to {1}% of it.")
				             .format(stage or _("the material"), round(share, 2)))
			if round(share, 2) < 99.5:
				msg = _("{0}% of the {1} has no seller against it.").format(
					round(100 - share, 2), stage or _("material"))
				if self.docstatus == 1 or self.flags.in_submit:
					frappe.throw(msg, title=_("Not all of it is bought"))
				frappe.msgprint(msg, indicator="orange", alert=True)

	@frappe.whitelist()
	def raising_choices(self):
		"""Units, propagators and what is already chosen, for the form's dialog."""
		from upande_summer_flowers.summer_flowers.root_line import raising_choices

		return raising_choices(self.production_plan)

	@frappe.whitelist()
	def set_raising(self, rows):
		"""Replace who raises this plan's material with what the dialog shows."""
		from upande_summer_flowers.summer_flowers.root_line import set_raising

		return set_raising(self.production_plan, rows)

	@frappe.whitelist()
	def ways_in(self):
		"""The materials this plan has to raise, for the grid to offer."""
		stages = [s for s in self.plants_by_stage() if s]
		return stages or [self.main_entry_stage() or ""]

	@frappe.whitelist()
	def propose_purchase(self):
		"""One seller per way in, to start from.

		The plan already names a supplier where one was chosen, so the table opens
		on that rather than on a blank somebody has to fill twice.
		"""
		if self.method != "Purchase":
			frappe.throw(_("This plan buys nothing, so there is nobody to buy from."))
		totals = self.units_by_stage() or {self.main_entry_stage() or "": 0}
		self.set("purchase_allocation", [])
		for stage in totals:
			self.append("purchase_allocation", {
				"entry_stage": stage, "supplier": self.supplier,
				"share_pct": 100, "lead_weeks": cint(self.lead_weeks)})
		self.save()
		return {"ways": len(totals), "stages": list(totals)}

	def suppliers_for(self, stage):
		"""The sellers of one way in, with their shares, biggest first."""
		rows = [r for r in (self.get("purchase_allocation") or [])
		        if (r.entry_stage or "") == (stage or "") and r.supplier]
		rows.sort(key=lambda r: -flt(r.share_pct))
		return rows

	def set_allocation_amounts(self):
		"""Who raises the plants, and when that means we get them.

		Buying the material and raising it are two decisions, not one: a crop
		bought as tissue culture is bought from a laboratory and raised by a unit
		or a propagator, and the second half used to go unrecorded, so the plan
		said what was ordered and nothing about when it became a plant.

		Our own unit takes what the protocol says propagation takes. A propagator
		takes the notice their own record gives. Either way the plants are wanted
		on the planting date, so that date less the weeks is when the sticking --
		or the order -- has to happen.
		"""
		receiving = None
		if self.farm and frappe.get_meta("Farm").get_field("custom_sf_plant_receiving_warehouse"):
			receiving = frappe.db.get_value("Farm", self.farm, "custom_sf_plant_receiving_warehouse")

		first, last = self.planting_window()
		plants = self.plants_by_stage()
		only = next(iter(plants)) if len(plants) == 1 else None
		main = self.main_entry_stage()

		for r in self.get("propagation_allocation") or []:
			# Which material this row raises. Rows written before the column
			# existed raised whatever the plan mostly buys, which is what they
			# were always taken to mean.
			if not r.raises:
				r.raises = only or main
			stage = r.raises or main or _("the material bought")
			# Rows written before the Type column existed named one party or the
			# other, so the type can be read back off them rather than guessed at.
			if not r.alloc_type:
				r.alloc_type = "Outside Propagator" if r.supplier else "Own Unit"
			outside = r.alloc_type == "Outside Propagator"

			if outside:
				r.propagation_unit = None
				sup = frappe.db.get_value(
					"Supplier", r.supplier,
					["custom_sf_propagation_weeks", "custom_sf_rate_per_plant"],
					as_dict=True) if r.supplier else None
				if sup and not cint(r.lead_weeks):
					r.lead_weeks = cint(sup.custom_sf_propagation_weeks)
				if sup and not flt(r.rate_per_plant):
					r.rate_per_plant = flt(sup.custom_sf_rate_per_plant)
				if not r.deliver_to:
					r.deliver_to = receiving
				r.ready_basis = _(
					"{0} needs {1} weeks' notice, so plants ordered on the start "
					"date land on the planting date."
				).format(r.supplier or _("The propagator"), cint(r.lead_weeks))
			else:
				r.supplier = None
				r.rate_per_plant = 0
				r.deliver_to = None
				# The protocol already answers this: the journey from what was
				# bought to a plant standing in a bed, which on our own bench is
				# exactly the propagation -- and it is asked of the material this
				# row actually raises.
				r.lead_weeks = self.weeks_to_raise(stage)
				r.ready_basis = _(
					"The protocol gives {0} weeks from {1} to a plant in the "
					"ground, raised on our own bench."
				).format(cint(r.lead_weeks), stage)

			r.party = r.supplier or r.propagation_unit
			# A share is a share of the material it raises, not of the season.
			if flt(r.share_pct) and not cint(r.plants):
				r.plants = int(round(cint(plants.get(stage, 0))
				                     * flt(r.share_pct) / 100.0))
			r.amount = cint(r.plants) * flt(r.rate_per_plant)
			r.plants_from, r.plants_to = first, last
			r.start_by = (add_days(first, -7 * cint(r.lead_weeks)) if first else None)

	def planting_window(self):
		"""First and last planting this plan has to have plants for.

		The cohort lines answer it where there are any. A plan that buys once and
		propagates writes none -- the plantings are the propagation plan's to
		schedule -- so the weeks come from the production plan's blocks, which
		decided them.
		"""
		dates = [getdate(r.planting_date) for r in (self.requirements or [])
		         if (r.line_type or "Planting") == "Planting" and r.planting_date]
		if not dates and self.production_plan:
			dates = [iso_monday(cint(b.planting_year), cint(b.planting_week))
			         for b in frappe.get_all(
				         "Summer Flower Plan Block",
				         filters={"parent": self.production_plan, "is_new_planting": 1},
				         fields=["planting_year", "planting_week"])
			         if cint(b.planting_week) and cint(b.planting_year)]
		return (min(dates), max(dates)) if dates else (None, None)

	@frappe.whitelist()
	def write_planting_plan(self):
		"""Write the plantings this plan has not written yet.

		Approval used to be the only moment this ran, and it ran once: a cohort
		with no block when the plan was approved never got an entry, because an
		approved plan cannot be approved again. Blocks are allocated on the
		production plan, usually afterwards, so that was most of them -- 236 of
		274 cohorts on this bench had no entry and nothing to make one.
		"""
		if self.docstatus != 1:
			frappe.throw(_("Approve this plan before writing its plantings."))
		before = frappe.db.count("Planting Calendar",
		                         {"variety": self.variety, "farm": self.farm})
		self.build_planting_plan()
		after = frappe.db.count("Planting Calendar",
		                        {"variety": self.variety, "farm": self.farm})
		return {"written": after - before}

	@frappe.whitelist()
	def resize_order(self, units):
		"""Re-plan this order at a quantity someone has decided on.

		The lever is the same whichever way the crop is got, and the consequence
		is not: on a pool route a smaller order is a smaller motherstock cutting
		less every week, and on a direct one it is simply plantings that get no
		plants. Both are worked out by re-planning, so the document cannot hold a
		quantity that its own lines were never sized for.
		"""
		units = cint(units)
		if units <= 0:
			frappe.throw(_("Give the number of units to order."))
		if self.docstatus != 0:
			frappe.throw(_("{0} is approved. Cancel it before changing what it buys.")
			             .format(self.name))
		pool = cint(self.pool_plants) > 0
		build(self.production_plan, method=self.method,
		      entry_stage=(self.entry_stage if " + " not in (self.entry_stage or "")
		                   else None),
		      supplier=self.supplier,
		      cycles=(cint(self.multiplication_cycles) if pool else None),
		      tc_qty=(units if pool else None),
		      units_qty=(None if pool else units))
		return self.name

	def check_sourcing(self):
		"""Where the material is bought, said as plainly as who raises it.

		Half the answer is useless: a plan that names the propagator but not the
		laboratory cannot order anything, and one that names the laboratory but
		not the propagator has plantlets arriving at a farm with no bench booked.
		A route bought more than one way names its seller per line, so the lines
		answer for themselves where the header cannot.
		"""
		if self.method != "Purchase":
			return
		# The purchase allocation is the answer where there is one: it names a
		# seller per way in, which is more than the single field above it can say.
		if any(r.supplier for r in (self.get("purchase_allocation") or [])):
			return
		if self.supplier or any(r.supplier for r in (self.requirements or [])):
			return
		msg = _("Nothing says where this plan buys its {0}. Name the supplier."
		        ).format(self.main_entry_stage() or _("material"))
		if self.docstatus == 1 or self.flags.in_submit:
			frappe.throw(msg, title=_("No seller named"))
		if not (self.is_new() or self.flags.get("rebuilding")):
			frappe.msgprint(msg, indicator="orange", alert=True)

	def check_allocation(self):
		"""Nobody raising the plants is not a plan, so it cannot be saved as one.

		Asked only of a plan that has something to raise, and not of a brand new
		one -- the plan is created from the production plan before anyone has been
		offered the units to choose from.
		"""
		# Re-planning rewrites the order, not the allocation, and it runs before
		# anyone has been offered the units. Judging the allocation on that save
		# refuses to re-plan an order over rows the re-plan does not touch.
		if not self.propagates() or self.flags.get("rebuilding"):
			return
		rows = self.get("propagation_allocation") or []
		if not rows and not self.is_new():
			frappe.throw(_("This plan buys {0} and the plants are weeks away from "
			               "it. Say who raises them -- our own unit, an outside "
			               "propagator, or both -- under Who Raises It.")
			             .format(self.main_entry_stage() or _("material")),
			             title=_("Nobody is raising these plants"))

		for r in rows:
			if r.alloc_type == "Outside Propagator":
				if not r.supplier:
					frappe.throw(_("Row {0} is raised outside, so it needs the "
					               "propagator's name.").format(r.idx))
				if not cint(r.lead_weeks):
					frappe.throw(_("{0} has no propagation lead time, so this plan "
					               "cannot say when the plants arrive. Set Weeks To "
					               "Raise A Plant on the supplier, or type the weeks "
					               "on row {1}.").format(r.supplier, r.idx),
					             title=_("No lead time"))
			elif not r.propagation_unit:
				frappe.throw(_("Row {0} is raised here, so it needs the unit that "
				               "raises it.").format(r.idx))

		# Per way in. A crop bought as tissue culture and as roots has both to
		# raise, and a unit taking all the tissue culture does not mean the roots
		# are covered -- counted together, 100% could be the whole of one and
		# none of the other.
		plants = self.plants_by_stage()
		by_stage = {}
		for r in rows:
			by_stage.setdefault(r.raises or "", 0)
			by_stage[r.raises or ""] += flt(r.share_pct)
		for stage in plants:
			share = by_stage.get(stage, 0)
			if round(share, 2) > 100.5:
				frappe.throw(_("The units and propagators raising the {0} add up "
				               "to {1}% of it.").format(stage or _("material"),
				                                        round(share, 2)))
			if round(share, 2) < 99.5:
				short = _("{0}% of the {1} -- about {2} plants -- has nobody "
				          "raising it.").format(
					round(100 - share, 2), stage or _("material"),
					"{:,}".format(int(cint(plants[stage]) * (100 - share) / 100)))
				if self.docstatus == 1 or self.flags.in_submit:
					frappe.throw(short + " " + _("Say who raises the rest before "
					                             "approving."),
					             title=_("Not all of it is raised"))
				frappe.msgprint(short, indicator="orange", alert=True)

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
		# Plants raised by an outside propagator are bought from them: one order
		# each, at their charge per plant, for their share of the season.
		self.raise_propagator_orders()
		# An allocation is itself the statement that this is raised here, so it
		# carries the same weight as the flag. Without this, choosing units and
		# then approving produced no propagation plans at all and said nothing.
		if cint(self.propagates_here) or self.get("propagation_allocation"):
			self.build_propagation_plan()
		# Whichever way the material was got, the farm needs to know what to plant
		# and when. How it was got only changes what the calendar records as its
		# source -- and which date it plants on.
		self.build_planting_plan()
		self.build_season_plan()

	def build_season_plan(self):
		"""The season on one page, for the managers who have to work it.

		Never fatal: it reads the calendars and stores nothing of its own, so a
		failure here costs a summary, not a plan.
		"""
		from upande_summer_flowers.summer_flowers.doctype \
			.summer_flower_season_plan.summer_flower_season_plan import for_season

		year = frappe.db.get_value("Summer Flower Production Plan",
		                           self.production_plan, "season_start_year")
		if not year:
			return
		try:
			name = for_season(self.variety, self.farm, year, self.production_plan)
		except Exception:
			frappe.log_error(frappe.get_traceback(),
			                 "Season plan for %s" % self.name)
			return
		frappe.msgprint(
			_("{0} has the season on one page: when the plants arrive, when each "
			  "block is planted, and when it first cuts.").format(
				frappe.utils.get_link_to_form("Summer Flower Season Plan", name)),
			indicator="green", title=_("Season planting plan"))

	def raise_propagator_orders(self):
		"""A draft Purchase Order per outside propagator on the allocation.

		The TC or seed is bought from its seller (raise_material_requests); the
		plants an outside propagator raises from it are bought again, from the
		propagator, at what they charge per plant. Their delivery is received as a
		Plant Consignment against this order, and the order is what gets invoiced.
		Left as drafts: submitting one commits money, approving a plan does not.
		"""
		item = frappe.db.get_value("Item", self.variety, "name")
		rows = [r for r in (self.get("propagation_allocation") or [])
		        if r.supplier and cint(r.plants) > 0 and not r.purchase_order]
		if not rows:
			return
		if not item:
			frappe.msgprint(_("No Item called {0}, so no order could be raised for the "
			                  "outside propagators.").format(self.variety), indicator="orange")
			return
		today = getdate(nowdate())
		dates = [getdate(r.required_at_site_date) for r in (self.requirements or [])
		         if r.required_at_site_date]
		if not dates:
			first, _last = self.planting_window()
			dates = [first] if first else []
		when = max(min(dates) if dates else today, today)
		made, failed = [], []
		for r in rows:
			# Into the warehouse the plants are actually delivered to. Never a
			# guess: an order booked into the wrong store puts stock where nobody
			# will find it, so with nowhere named the order is not raised.
			warehouse = r.deliver_to
			if not warehouse:
				failed.append(_("{0}: no warehouse to deliver to. Set Plant Receiving "
				                "Warehouse on farm {1}, or Deliver To on the allocation "
				                "row.").format(r.supplier, self.farm))
				continue
			po = frappe.new_doc("Purchase Order")
			po.supplier = r.supplier
			po.company = self.company
			po.transaction_date = today
			po.schedule_date = when
			if po.meta.get_field("set_warehouse"):
				po.set_warehouse = warehouse
			row = {"item_code": item, "qty": cint(r.plants), "rate": flt(r.rate_per_plant),
			       "schedule_date": when, "warehouse": warehouse,
			       "description": _("{0} plants raised by {1} for {2}").format(
				       self.variety, r.supplier, self.get("season") or self.name)}
			if frappe.get_meta("Purchase Order Item").get_field("custom_purpose"):
				row["custom_purpose"] = _("{0} propagation, {1}").format(
					self.variety, self.get("season") or self.name)
			po.append("items", row)
			po.flags.ignore_permissions = True
			try:
				po.insert()
			except Exception as e:
				frappe.log_error(frappe.get_traceback(),
				                 "Propagator order for %s (%s)" % (self.name, r.supplier))
				failed.append("%s: %s" % (r.supplier, str(e).split("\n")[0][:160]))
				continue
			r.db_set("purchase_order", po.name, update_modified=False)
			made.append("%s (%s)" % (po.name, r.supplier))
		if made:
			frappe.msgprint(_("Raised as drafts for the outside propagators: {0}").format(
				", ".join(made)), indicator="green", title=_("Propagator orders"))
		if failed:
			frappe.msgprint(_("Could not raise: {0}. The error is in the log; raise these by "
			                  "hand.").format("; ".join(failed)), indicator="orange",
			                title=_("Propagator orders"))

	def _draft_order(self, mr, supplier, failed):
		"""Submit the request and draft the order it becomes. Returns its name.

		ERPNext maps a Material Request to a Purchase Order only from a
		submitted request, so the request is submitted here. Nothing is
		promised by that -- a submitted request is the farm asking for the
		material, and the order it drafts is where money is committed, which is
		why the order stays a DRAFT. Submitting the order is the confirmation:
		it is what the supplier has agreed to send and when, and until somebody
		has that from them there is nothing to submit.

		The dates come across by themselves. get_schedule_dates on the order
		reads each line's required-by off the request line it was mapped from,
		so a request with fourteen deliveries in it drafts an order with
		fourteen, each on its own week.
		"""
		from erpnext.stock.doctype.material_request.material_request import (
			make_purchase_order,
		)

		if not supplier:
			# Nobody to buy from. The request stands as the record of what is
			# needed; who sells it is the purchase allocation's answer and it has
			# not been given yet.
			return None
		try:
			mr.submit()
		except Exception as e:
			frappe.log_error(frappe.get_traceback(),
			                 "Submitting %s for %s" % (mr.name, self.name))
			failed.append(_("{0} could not be submitted: {1}").format(
				mr.name, str(e).split("\n")[0][:120]))
			return None
		try:
			po = make_purchase_order(mr.name)
			po.supplier = supplier
			if not po.get("schedule_date"):
				po.schedule_date = mr.schedule_date
			po.flags.ignore_permissions = True
			po.insert()
			return po.name
		except Exception as e:
			frappe.log_error(frappe.get_traceback(),
			                 "Drafting an order from %s" % mr.name)
			failed.append(_("no order could be drafted from {0}: {1}").format(
				mr.name, str(e).split("\n")[0][:120]))
			return None

	def raise_material_requests(self):
		"""One Material Request per supplier, with a line per delivery.

		Not one per cohort, and no longer one per week either. A season bought
		from one supplier is one order with twenty-six deliveries in it, and
		that is a thing Material Request and Purchase Order already model: each
		item row carries its own schedule_date, PurchaseOrder.get_schedule_dates
		copies it across from the request line it came from, and
		validate_schedule_date sets the header to the earliest of them. Raising
		one request a week instead produced twenty-six requests and twenty-six
		orders for one supplier and one crop, and a buyer chasing a lab had to
		open all of them to see the season.

		They are left as drafts -- submitting one is a person committing money,
		and approving a plan is not that.
		"""
		from collections import defaultdict

		item = frappe.db.get_value("Item", self.variety, "name")
		if not item:
			frappe.msgprint(_("No Item called {0}, so nothing can be requested for it.")
			                .format(self.variety), indicator="orange")
			return

		# Split each line between the sellers of its own way in. Grouping on the
		# line's own supplier sent the whole order to one of them, which is the
		# single supplier field the purchase allocation exists to replace -- a
		# crop bought from two laboratories got one request and the second lab
		# never heard about it.
		groups = defaultdict(list)
		for r in self.requirements:
			if not cint(r.qty_to_order) or r.source_doc:
				continue
			sellers = self.suppliers_for(r.entry_stage or self.entry_stage)
			if not sellers:
				groups[r.supplier].append(r)
				continue
			shares = [flt(x.share_pct) for x in sellers]
			qty, taken = cint(r.qty_to_order), 0
			for i, seller in enumerate(sellers):
				part = (qty - taken if i == len(sellers) - 1
				        else int(round(qty * shares[i] / 100.0)))
				taken += part
				if part <= 0:
					continue
				# A copy of the line carrying one seller's share. The line on the
				# document is untouched: it is what the plan asked for, and this
				# is how it was bought.
				share_of = frappe._dict(r.as_dict())
				share_of.qty_to_order = part
				share_of.supplier = seller.supplier
				# The row on the document this copy speaks for. A copy is a plain
				# dict and has no db_set, so writing the request back onto it threw
				# away the link on every crop bought from more than one seller --
				# and then failed outright, because db_set on a _dict is None and
				# None is not callable.
				share_of._line = r
				groups[seller.supplier].append(share_of)

		made, ordered, overdue, failed = [], [], [], []
		today = getdate(nowdate())
		def wanted_on(row):
			return (getdate(row.required_at_site_date)
			        if row.required_at_site_date else today)

		for supplier, rows in sorted(
			groups.items(), key=lambda kv: min(wanted_on(r) for r in kv[1])
		):
			rows.sort(key=wanted_on)
			mr = frappe.new_doc("Material Request")
			mr.material_request_type = "Purchase"
			mr.company = self.company
			mr.transaction_date = today
			# A Material Request refuses a required-by date before its transaction
			# date -- per row, not just on the header -- and the whole approval used
			# to die on it, so a plan whose first order date had passed could not be
			# approved at all, which is exactly the plan someone most needs to
			# approve and chase. The date each line wanted is kept on its requirement
			# row; what goes on the request is the soonest it can honestly be asked
			# for, and the plan says which were already late.
			late = [r for r in rows if wanted_on(r) < today]
			if late:
				overdue.extend((wanted_on(r), r) for r in late)
			# Set here only so the field is filled before the mandatory check;
			# validate_schedule_date works out the same earliest-of-the-lines.
			mr.schedule_date = max(min(wanted_on(r) for r in rows), today)
			if mr.meta.get_field("custom_farm"):
				mr.custom_farm = self.farm
			if mr.meta.get_field("custom_purpose"):
				mr.custom_purpose = "Production"
			for r in rows:
				row = {
					"item_code": item,
					"qty": cint(r.qty_to_order),
					# The delivery this line is for. One order, many dates: this
					# is the whole of the staggered delivery, and ERPNext carries
					# it to the Purchase Order Item without being asked.
					"schedule_date": max(wanted_on(r), today),
					"warehouse": frappe.db.get_value("Warehouse",
					                                 {"company": self.company,
					                                  "is_group": 0}, "name"),
					"description": _("{0} as {1} for planting {2}").format(
						self.variety,
						r.entry_stage or self.entry_stage or _("plants"),
						r.planting_week or ""),
				}
				# A site field, but one this chain cannot skip: custom_purpose is
				# mandatory on BOTH Material Request Item and Purchase Order Item
				# here, and the mapping carries it from one to the other. Leaving
				# it empty let the request be raised and then stopped the order
				# dead -- 64,477 of the site's 64,486 request lines carry it, and
				# the ones this module raised were among the nine that did not.
				if mr.meta.get_field("items").options and frappe.get_meta(
						"Material Request Item").get_field("custom_purpose"):
					row["custom_purpose"] = _("{0} planting material, {1}").format(
						self.variety, self.get("season") or self.name)
				mr.append("items", row)
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
			# Paired by position: the item rows were appended from `rows` in this
			# order, and one requirement line is one delivery is one request line.
			for src, item in zip(rows, mr.items):
				line = src.get("_line") if isinstance(src, dict) else None
				line = line if line is not None else src
				if not hasattr(line, "db_set"):
					continue
				line.db_set("source_doctype", "Material Request", update_modified=False)
				line.db_set("source_doc", mr.name, update_modified=False)
				line.db_set("source_row", item.name, update_modified=False)
			made.append(mr.name)
			order = self._draft_order(mr, supplier, failed)
			if order:
				ordered.append(order)
			for row in self.get("purchase_allocation") or []:
				if row.supplier == supplier and not row.material_request:
					row.db_set("material_request", mr.name, update_modified=False)
					if order:
						row.db_set("purchase_order", order, update_modified=False)
					break

		if made:
			frappe.msgprint(_("{0} raised as drafts: {1}").format(
				len(made), ", ".join(made)), indicator="green",
				title=_("Material Requests"))
		if ordered:
			frappe.msgprint(_(
				"{0} drafted and waiting to be confirmed: {1}. Each carries one line "
				"a delivery. Confirm what the supplier can actually send, week by "
				"week, on the plan's Procurement tab -- submitting the order is the "
				"act of agreeing that schedule."
			).format(len(ordered), ", ".join(ordered)), indicator="blue",
				title=_("Purchase Orders"))
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
		# Hung off `if overdue` until now, so a plan that raised its orders and had
		# nothing late said "Nothing to order" in the same breath as "3 raised as
		# drafts". Nothing to order is a statement about what was raised.
		if not made and not failed:
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
		return write_plantings(
			frappe.get_doc("Summer Flower Production Plan", self.production_plan), self)

	@frappe.whitelist()
	def allocation_pool(self):
		"""The plantings a share is a share OF.

		Typing 30% into the grid means nothing until it is 30% of something, and
		the something has to be the same figure `propose_allocation` divides --
		the production plan's weekly plantings summed -- or the two ways of
		filling the same table would disagree with each other.
		"""
		# What THIS plan buys, not what the production plan wanted. An order cut
		# to 4,000 was still divided against the 6,000 the plantings asked for,
		# so a unit taking everything the plan actually buys read as 67%.
		if cint(self.plants_from_order):
			return {"season_plants": cint(self.plants_from_order),
			        "basis": _("what this order makes")}
		try:
			from upande_propagation.allocation import plan_weeks
		except ImportError:
			return {"season_plants": cint(self.total_plants_at_field),
			        "basis": _("the plan's plants to stand")}
		if not self.production_plan:
			return {"season_plants": 0, "basis": ""}
		try:
			total = int(sum(q for _d, q in plan_weeks(self.production_plan)))
		except Exception:
			total = cint(self.total_plants_at_field)
		return {"season_plants": total,
		        "basis": _("the production plan's weekly plantings, summed")}

	def main_entry_stage(self):
		"""The stage the most of this order comes in as.

		entry_stage reads as a summary where a route is bought more than one way
		-- "TC 65% + Roots 35%" -- and that is a label, not a stage. Handing it to
		anything that looks a stage up by name asks for a form that does not
		exist, so the question is answered from the lines, which each name one.
		"""
		weight = {}
		for r in (self.requirements or []):
			if r.entry_stage:
				weight[r.entry_stage] = weight.get(r.entry_stage, 0) + cint(r.qty_at_field)
		if weight:
			return max(weight, key=weight.get)
		return self.entry_stage

	@frappe.whitelist()
	def propose_allocation(self, units=None, suppliers=None):
		"""Fill the allocation from what the units' troughs will actually hold.

		Offered after the plan is made, not before: the material is bought the
		same way whichever unit raises it, so the buying decision does not wait
		on this one. What this decides is who raises it -- and the honest answer
		comes from the troughs rather than from a share typed in, because a
		share the benches cannot hold is a shortfall discovered in week forty.

		Units are offered the season in turn; what none can take is what has to
		be bought in as plants.
		"""
		try:
			from upande_propagation import allocation as A
		except ImportError:
			frappe.throw(_("Trough allocation needs the upande_propagation app, "
			               "which is not installed on this site."))

		if isinstance(units, str):
			units = frappe.parse_json(units)
		if isinstance(suppliers, str):
			suppliers = frappe.parse_json(suppliers)

		rates = {}
		if not units and not suppliers:
			# Our own units are the warehouses of the propagation unit type (set in
			# Summer Flower Settings) -- the one rule the board and forms use too.
			from upande_summer_flowers.summer_flowers.settings import propagation_unit_type

			f = {"warehouse_type": propagation_unit_type(), "disabled": 0, "is_group": 0}
			if self.get("company"):
				f["company"] = self.company
			units = frappe.get_all("Warehouse", filters=f, pluck="name")
		if not units and not suppliers:
			frappe.throw(_("No propagation unit is marked on any warehouse, so there "
			               "is nobody to allocate to."))

		# Outside propagators take what our benches cannot, when they are named --
		# on the protocol or here. The plan's own supplier is NOT one of them: that
		# is who sells the TC or seed, and a lab is not a propagator. What nobody
		# can take is reported as left to raise, not handed to the seller.
		if suppliers:
			suppliers = [{"supplier": x} if isinstance(x, str) else x
			             for x in suppliers]
			# A charge per plant given with the propagator is its charge.
			rates.update({x["supplier"]: flt(x.get("rate_per_plant")) for x in suppliers
			              if x.get("supplier") and flt(x.get("rate_per_plant"))})

		# Two ways to decide, and they answer different questions.
		#
		# By capacity: offer the season to each unit in turn and let the troughs
		# say what they can hold. The honest default, because a share the
		# benches cannot hold is a shortfall discovered in week forty.
		#
		# By share: somebody has decided to send a fixed part of the season
		# outside -- to spread the risk, to honour a contract, or to keep a
		# bench free. That is a decision about the business, not about the
		# troughs, so it is obeyed and the troughs then report what the share
		# actually costs in surplus.
		want_shares = any(flt((x or {}).get("share_pct")) for x in (units or []) + (suppliers or [])
		                  if isinstance(x, dict))
		if want_shares:
			dests = []
			for u in units:
				if isinstance(u, dict) and u.get("share_pct") is not None:
					dests.append({"unit": u.get("unit") or u.get("propagation_unit"),
					              "share_pct": flt(u["share_pct"])})
				else:
					dests.append({"unit": u if isinstance(u, str) else u.get("unit"),
					              "share_pct": 0})
			for sup in (suppliers or []):
				dests.append({"supplier": sup.get("supplier"),
				              "share_pct": flt(sup.get("share_pct"))})
			res = A.allocate(self.production_plan, dests,
			                 form=self.main_entry_stage() or "Roots",
			                 version=self.protocol)
		else:
			res = A.allocate_by_capacity(self.production_plan, units=units,
			                             suppliers=suppliers,
			                             form=self.main_entry_stage() or "Roots",
			                             version=self.protocol)
		form = self.main_entry_stage() or "Roots"
		self.set("propagation_allocation", [])
		for d in res["destinations"]:
			if d.get("kind") == "own unit":
				self.append("propagation_allocation", {
					"alloc_type": "Own Unit", "raises": form,
					"propagation_unit": d["unit"], "plants": d["plants"],
					"share_pct": d["share_pct"], "planted": d.get("planted"),
					"surplus": d.get("surplus"), "troughs_used": d.get("troughs_used"),
					"plantings": d.get("plantings"), "note": d.get("reason"),
				})
			elif d.get("kind") == "propagator":
				# What our own benches cannot hold is raised off the farm. The
				# allocator has always worked these out; they were dropped on the
				# way to the table because there was nowhere to put a supplier,
				# so a plan that sent 80% of its season outside showed only the
				# 20% it kept.
				self.append("propagation_allocation", {
					"alloc_type": "Outside Propagator", "raises": form,
					"supplier": d.get("supplier"), "plants": d["plants"],
					"share_pct": d["share_pct"],
					"rate_per_plant": rates.get(d.get("supplier"), 0),
					"note": _("Raised off the farm · {0} weeks · {1} weeks' lead")
						.format(d.get("weeks"), d.get("lead_weeks")),
				})
		self.save()
		return {"allocated": sum(cint(r.plants) for r in self.propagation_allocation),
		        "season": res["season_plants"],
		        "to_buy": res.get("unallocated", 0),
		        "units": len(self.propagation_allocation)}

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

		allocated = [r for r in (self.get("propagation_allocation") or [])
		             if r.alloc_type != "Outside Propagator"
		             and r.propagation_unit and cint(r.plants) > 0]
		if allocated:
			return self._build_allocated_plans(allocated, build_from_plan)

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

	def _build_allocated_plans(self, allocated, build_from_plan):
		"""One propagation plan per allocated unit.

		The uniqueness rule is variety + season + unit, so these sit beside each
		other instead of overwriting one another -- which is what happened while
		it was variety + season alone.
		"""
		made = []
		for row in allocated:
			try:
				outcome = build_from_plan(self.production_plan, as_dict=True,
				                          propagation_unit=row.propagation_unit)
			except TypeError:
				# An older build_from_plan that does not take a unit: make the
				# plan, then stamp the unit on it so the rule can tell them apart.
				outcome = build_from_plan(self.production_plan, as_dict=True)
				frappe.db.set_value("Summer Flower Propagation Plan", outcome["name"],
				                    "propagation_unit", row.propagation_unit)
			except Exception:
				frappe.log_error(frappe.get_traceback(),
				                 "Propagation plan for %s at %s" % (self.name,
				                                                    row.propagation_unit))
				continue
			row.db_set("propagation_plan", outcome["name"], update_modified=False)
			self._carry_tc_choice(outcome["name"])
			made.append("%s (%s)" % (outcome["name"], row.propagation_unit))
		if made:
			frappe.msgprint(_("Propagation plans raised: {0}").format(", ".join(made)),
			                indicator="green", title=_("Propagation"))
		return made

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

	@frappe.whitelist()
	def overview_for_form(self):
		"""The dashboard the form draws, as a document method so the form can call
		it without repeating the name."""
		return overview(self.name)

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	def set_totals(self):
		rows = self.requirements or []
		# The establishment line is the motherstock, not a planting: its qty_at_field
		# is mother plants standing on a bench, and adding it to the plants going in
		# the ground would overstate the crop by the size of the pool.
		field = [r for r in rows if (r.line_type or "Planting") != "Establishment"]
		self.total_units_to_order = sum(cint(r.qty_to_order) for r in rows)
		if field:
			self.total_plants_at_field = sum(cint(r.qty_at_field) for r in field)
			self.beds_required = sum(cint(r.beds) for r in field)
		elif self.production_plan:
			# A buy-once plan writes no cohort lines -- the plantings are the
			# propagation plan's to schedule -- so what goes in the ground is read
			# from the plan that decided it, rather than adding up rows that are
			# deliberately not here.
			plan = frappe.db.get_value(
				"Summer Flower Production Plan", self.production_plan,
				["new_plants_required", "new_beds_required"], as_dict=True)
			self.total_plants_at_field = cint(plan.new_plants_required) if plan else 0
			self.beds_required = cint(plan.new_beds_required) if plan else 0
		dates = [getdate(r.order_by_date) for r in rows if r.order_by_date]
		self.first_order_by = min(dates) if dates else None
		self.last_order_by = max(dates) if dates else None
		today = getdate(nowdate())
		for r in rows:
			r.late = 1 if (r.order_by_date and getdate(r.order_by_date) < today) else 0
		self.orders_late = sum(1 for r in rows if r.late)
		self.capped_by_space = 1 if any(cint(r.capped) for r in rows) else 0


def trim_to_order(built, budget, ppb):
	"""Serve a smaller order through the plantings, earliest first.

	A plan that buys plants or roots one per plant has nothing to multiply, so
	buying fewer is not a smaller pool -- it is fewer plantings. They are served
	in planting order, which is what the farm does with a short delivery and the
	same rule the ground shortage already follows: the early weeks go in full and
	what runs out runs out at the end of the season.

	Returns what the order plants, what it leaves unplanted, and how many
	plantings it cannot fill.
	"""
	wanted = sum(cint(line.qty_at_field) for line, _pu in built)
	# A route bought two ways is still bought in its own shares when less of it
	# is bought. Serving the lines in one queue let the first cohort's tissue
	# culture swallow the whole order and left 217 roots against 14,903 -- a
	# route nobody chose, arrived at by the order the lines happen to sit in.
	ways, order = {}, []
	for line, per_unit in built:
		key = line.entry_stage or ""
		if key not in ways:
			ways[key] = []
			order.append(key)
		ways[key].append((line, per_unit))
	asked = {k: sum(cint(l.qty_to_order) for l, _pu in v) for k, v in ways.items()}
	total_asked = sum(asked.values()) or 1
	budgets, given = {}, 0
	for i, k in enumerate(order):
		budgets[k] = (cint(budget) - given if i == len(order) - 1
		              else int(round(cint(budget) * asked[k] / float(total_asked))))
		given += budgets[k]

	made, short_cohorts = 0, set()
	for k in order:
		left = max(0, budgets[k])
		for line, per_unit in ways[k]:
			want_units = cint(line.qty_to_order)
			take = max(0, min(want_units, left))
			left -= take
			if take < want_units:
				short_cohorts.add(line.cohort)
				line.qty_to_order = take
				line.qty_at_field = int(take * flt(per_unit)) if per_unit else 0
				line.short_ordered = 1
			made += cint(line.qty_at_field)
	# Beds follow the plants, not the other way round: a cohort that gets half
	# its plants fills half its beds, and leaving the original count there books
	# ground that nothing is going into.
	first_of = {}
	plants_of = {}
	for line, _pu in built:
		plants_of[line.cohort] = plants_of.get(line.cohort, 0) + cint(line.qty_at_field)
		first_of.setdefault(line.cohort, line)
	if ppb:
		for cohort, line in first_of.items():
			if line.cohort in short_cohorts:
				line.beds = int(math.ceil(plants_of[cohort] / float(ppb)))
	return {"wanted": wanted, "made": made, "short": max(0, wanted - made),
	        "cohorts_short": len(short_cohorts)}


def write_plantings(plan, proc=None):
	"""Turn a plan's cohorts into Planting Calendar entries.

	The calendar is what the farm works from, and it is the same document
	whichever way the plants were got -- only seedling_source, and the supplier
	or batch beside it, differ.

	`proc` is the approved procurement plan where there is one. A route that
	takes its material off the farm's own crop -- own roots, own tubers, own
	cuttings -- has nothing to buy and therefore no procurement plan, and those
	crops still have to be planted. Writing this on the procurement plan alone
	left them with no writer at all.
	"""
	# What the bench can actually deliver, week by week. The production plan says
	# what the market wants stuck; the propagation plan says what there are
	# cuttings for. Writing the first into the calendar as though it were the
	# second is how a farm comes to expect plants that were never raised.
	covered = proc._propagation_coverage() if proc else {}
	# What the farm plants is what came off its own bench whenever anything is
	# raised here, even though the tissue culture behind it was bought. The
	# supplier delivered a plantlet, not a plant.
	source = ("In-house Propagation" if (not proc or cint(proc.propagates_here))
	          else "Purchased from Breeder")
	made, skipped, at_risk, refused, already, moved = [], 0, [], [], 0, []
	# A block on a plan row is a claim made when the row was allocated, and
	# nothing re-checks it. Approve in September, let another plan take the
	# beds in October, and the calendar is the only thing that notices -- by
	# refusing thirty-two of thirty-eight plantings. So the claim is tested
	# here, against what holds the beds now, and a cohort whose block has
	# gone is given another one rather than lost.
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_production_plan.summer_flower_production_plan import (
			BlockCalendar,
		)

	protocol = (proc.protocol if proc else plan.protocol)
	if not protocol:
		frappe.throw(_("{0} names no protocol version, so there is nothing to "
		               "say how long a planting holds its block. Re-plan it "
		               "from the demand.").format(plan.name),
		             title=_("No protocol"))
	v = frappe.get_cached_doc("Crop Protocol Version", protocol)
	life = datetime.timedelta(weeks=cint(v.total_weeks_in_ground))
	cal = BlockCalendar(plan.farm, plan.variety)

	# Off the production plan's own plantings. The requirement lines are the
	# purchase, and on a buy-once route there are none per cohort -- reading the
	# calendar off them wrote nothing at all for exactly the crops that need it.
	rows = [b for b in plan.plan_blocks if b.is_new_planting]
	rows.sort(key=lambda b: getdate(b.planting_date or nowdate()))
	for row in rows:
		r = next((x for x in (proc.requirements if proc else [])
		         if x.cohort == row.name), None)
		plants_here = cint(r.qty_at_field) if r else cint(row.plants)
		if not plants_here:
			continue
		if row.existing_planting and frappe.db.exists("Planting Calendar",
		                                              row.existing_planting):
			already += 1
			continue
		if not row.planting_date:
			skipped += 1
			continue
		start = getdate(row.planting_date)
		end = start + life
		beds_wanted = cint(row.beds) or 1
		block = row.block
		if block and cal.get(block) and cal.free_beds(cal.get(block), start, end) >= beds_wanted:
			cal.reserve(cal.get(block), start, end, beds_wanted)
		else:
			# Either never allocated, or allocated to beds somebody else now
			# holds. Same answer either way: find beds that are free now.
			fresh = cal.place(beds_wanted, start, end)
			if not fresh:
				skipped += 1
				continue
			if block and fresh != block:
				moved.append((row.name, block, fresh))
			block = fresh
			row.db_set("block", block, update_modified=False)
			row.db_set("not_placed", 0, update_modified=False)
		short, asked = covered.get((cint(row.sticking_year), cint(row.sticking_week)),
		                           (0, 0))
		shortfall_note = None
		if short and asked:
			shortfall_note = _(
				"The propagation plan is {0} cuttings short of the {1} this week "
				"needs, so this planting may go in smaller than {2} plants or "
				"later than {3}."
			).format(f"{short:,}", f"{asked:,}", f"{plants_here:,}",
			         row.planting_date)
		entry = frappe.get_doc({
			"doctype": "Planting Calendar",
			"block": block,
			"farm": plan.farm,
			"variety": plan.variety,
			"crop_protocol_version": protocol,
			"company": plan.company,
			"beds": cint(row.beds),
			"plants": plants_here,
			# The delivery date is the planting date only where what arrives IS
			# the plant. Where the farm propagates, the delivery is tissue
			# culture months earlier, and planting on it put the crop in the
			# ground before it existed.
			"planting_date": (row.planting_date if (not proc or cint(proc.propagates_here)) or not r
			                  else (r.expected_delivery_date or row.planting_date)),
			"sticking_date": row.sticking_date if row.get("sticking_date") else None,
			"seedling_source": source,
			"supplier": (proc.supplier if proc and proc.method == "Purchase"
		             and not cint(proc.propagates_here) else None),
		})
		if shortfall_note:
			entry.notes = ((entry.get("notes") or "") + "\n" + shortfall_note).strip() \
				if entry.meta.has_field("notes") else entry.get("notes")
			at_risk.append(row.planting_date)
		entry.flags.ignore_permissions = True
		entry.flags.built_in_bulk = True
		# One cohort the calendar refuses -- a protocol version that takes effect
		# after the planting date, a block rule this module knows nothing about --
		# must not cost the farm the other twenty-five and the propagation plan
		# built moments ago. Record which, and carry on.
		try:
			entry.insert()
		except Exception as e:
			# The calendar is the authority on its own beds, and the two counts
			# can still part company at an edge -- a planting that ends on the day
			# another starts. Rather than argue, take its answer and ask it for
			# somewhere else once; what it refuses twice is genuinely full.
			again = (cal.place(beds_wanted, start, end)
			         if "beds" in str(e) else None)
			if again and again != block:
				entry.block = again
				try:
					entry.insert()
					moved.append((row.name, block, again))
					block = again
				except Exception:
					again = None
			if not again or entry.get("__islocal"):
				frappe.log_error(frappe.get_traceback(),
				                 "Planting calendar for %s" % plan.name)
				refused.append("%s: %s" % (row.planting_date,
				                           str(e).split("\n")[0][:160]))
				continue
		if block != row.block:
			row.db_set("block", block, update_modified=False)
		row.db_set("existing_planting", entry.name, update_modified=False)
		made.append(entry.name)

	if made:
		frappe.msgprint(_("{0} planting calendar entries created.").format(len(made)),
		                indicator="green", title=_("Planting plan"))
	# Saying nothing is what an approval did when every cohort already had its
	# entry: no calendar appeared, none was needed, and the reader was left to
	# work out which of those it was.
	if already and not made:
		frappe.msgprint(
			_("All {0} plantings already have a calendar entry, so nothing new "
			  "was written. They are on the Planting Calendar for {1} at {2}."
			  ).format(already, plan.variety, plan.farm),
			indicator="blue", title=_("Planting plan"))
	elif already:
		frappe.msgprint(
			_("{0} plantings already had an entry and were left alone.").format(
				already), indicator="blue", title=_("Planting plan"))
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
	if moved:
		frappe.msgprint(_(
			"{0} planting(s) had to move: the beds they were allocated are held "
			"by something else now. {1}. The plan rows carry the new blocks."
		).format(len(moved), "; ".join("%s -> %s" % (a, b)
		                               for _r, a, b in moved[:3])),
			indicator="blue", title=_("Blocks re-allocated"))
	if skipped:
		frappe.msgprint(_(
			"{0} planting(s) could not be written: there are no beds free at "
			"{1} for the whole of their life. Find more ground, or move them."
		).format(skipped, plan.farm), indicator="orange",
			title=_("Nowhere to put them"))

@frappe.whitelist()


@frappe.whitelist()
def write_plantings_for(production_plan):
	"""The planting plan for a production plan, however its material is got.

	With an approved procurement plan, that plan writes it -- it is the only
	document that knows the source, the supplier and what each cohort's order
	actually delivers. Without one, because the route takes its material off the
	farm's own crop, the plan is written from the cohorts alone.
	"""
	plan = frappe.get_doc("Summer Flower Production Plan", production_plan)
	if plan.docstatus != 1:
		frappe.throw(_("Approve {0} before writing its plantings.").format(plan.name))
	q = frappe.db.get_value("Summer Flower Procurement Plan",
	                        {"production_plan": plan.name, "docstatus": 1}, "name")
	proc = frappe.get_doc("Summer Flower Procurement Plan", q) if q else None
	before = frappe.db.count("Planting Calendar",
	                         {"variety": plan.variety, "farm": plan.farm})
	write_plantings(plan, proc)
	after = frappe.db.count("Planting Calendar",
	                        {"variety": plan.variety, "farm": plan.farm})
	return {"written": after - before, "bought_through": q}


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


def existing_for(production_plan):
	"""The procurement plan already sourcing this one, and whether it can be revised.

	Almost always it can. A draft is rewritten in place, keeping its number and its
	history; a submitted plan is cancelled and amended, which is frappe's own way
	of revising one and leaves the original there to read.

	The single refusal is a submitted plan whose order date has come round: the
	material is on its way, and rewriting the order then buys it twice. That is not
	a warning about tidiness, so it is the only one left.
	"""
	row = frappe.db.get_value(
		"Summer Flower Procurement Plan",
		{"production_plan": production_plan, "docstatus": ["<", 2]},
		["name", "docstatus", "status", "first_order_by", "total_units_to_order",
		 "entry_stage"], as_dict=True)
	if not row:
		return None
	today = getdate(nowdate())
	ordered = bool(row.first_order_by and getdate(row.first_order_by) <= today)
	if row.docstatus == 1 and ordered:
		why = _("{0} is approved and its order was due {1}, which has passed -- the "
		        "material is on its way. Revising it now would order it a second "
		        "time. Cancel {0} first if that order never went out."
		        ).format(row.name, frappe.format(row.first_order_by,
		                                         {"fieldtype": "Date"}))
	else:
		# A submitted plan with nothing ordered yet is amended, not refused. It used
		# to be refused on the grounds that its material requests would be left
		# behind -- but cancelling it cancels those with it, which is exactly what
		# amending is for.
		why = None
	return {
		"name": row.name, "docstatus": row.docstatus, "status": row.status,
		"first_order_by": str(row.first_order_by or ""),
		"units": cint(row.total_units_to_order), "entry_stage": row.entry_stage,
		"order_date_passed": ordered,
		"can_replace": why is None, "why_not": why,
	}


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
		# What already sources this plan, so the dialog can offer to replace it
		# rather than letting Generate fail on it.
		"existing": existing_for(p.name),
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
def overview(name):
	"""What an approved procurement plan has committed the farm to.

	A plan that has been approved stops being a decision and becomes a set of
	dates: material ordered by, material arriving, plants going in, stems coming
	off. Until now the document said what to buy and nothing about what buying it
	gets you, so the one question anybody asks of an approved plan -- when does
	this turn into flowers, and does it meet the demand -- had to be answered by
	opening three other documents.
	"""
	q = frappe.get_doc("Summer Flower Procurement Plan", name)
	p = frappe.get_doc("Summer Flower Production Plan", q.production_plan)
	v = frappe.get_cached_doc("Crop Protocol Version", q.protocol)

	rows = [r for r in q.requirements if (r.line_type or "Planting") == "Planting"]
	est = next((r for r in q.requirements
	            if (r.line_type or "") == "Establishment"), None)

	# When each cohort lands and what it first cuts. first_harvest_week is written
	# when the plan is built, so this is the plan's own answer, not a second one.
	arrivals = []
	for r in sorted(rows, key=lambda x: getdate(x.planting_date or nowdate())):
		arrivals.append({
			"cohort": r.cohort,
			"order_by": str(r.order_by_date or ""),
			"arrives": str(r.expected_delivery_date or r.required_at_site_date or ""),
			"planting_week": r.planting_week,
			"planting_date": str(r.planting_date or ""),
			"first_harvest_week": r.first_harvest_week,
			"plants": cint(r.qty_at_field),
			"beds": cint(r.beds),
			"units": cint(r.qty_to_order),
			"from_pool": cint(r.drawn_from_pool),
			"late": cint(r.late),
		})

	firsts = [a["first_harvest_week"] for a in arrivals if a["first_harvest_week"]]
	plantings = [a["planting_date"] for a in arrivals if a["planting_date"]]

	prop = frappe.db.get_value(
		"Summer Flower Propagation Plan",
		{"production_plan": q.production_plan, "docstatus": ["<", 2]},
		["name", "status", "total_cuttings_required", "cuttings_uncovered",
		 "plants_short", "stems_at_risk", "first_sticking_date"], as_dict=True)

	# What the plan grows against what the register asked for. The procurement plan
	# does not recompute either: it reports the plan's own figures, so it cannot
	# disagree with the document it was built from.
	stems = cint(p.total_production_stems)
	# A plan approved before the cycle count was recorded carries a zero, and a
	# zero here reads as "no multiplication" rather than "not written down".
	cycles = cint(q.multiplication_cycles) or cint(v.max_multiplication_cycles)
	# The propagation plan is keyed on variety and season, so it can have been
	# built from a bigger plan than this one and report more stems at risk than
	# this plan grows. Reported as more than everything, it turned coverage
	# negative -- minus fifty-four per cent of a demand that is being over-met.
	raw_risk = cint(prop.stems_at_risk) if prop else 0
	at_risk = min(raw_risk, stems)
	risk_exceeds_plan = raw_risk > stems
	return {
		"plan": q.name,
		"status": q.status,
		"submitted": q.docstatus == 1,
		"variety": q.variety, "farm": q.farm,
		"production_plan": p.name, "season": p.get("season"),
		"buying": {
			"method": q.method,
			"entry_stage": q.entry_stage,
			"units": cint(q.total_units_to_order),
			"calculated": cint(q.calculated_units),
			"overridden": cint(q.units_overridden),
			"supplier": q.supplier,
			"cycles": cycles,
			"pool": cint(q.pool_plants),
			"weekly_draw": cint(q.weekly_draw),
			"makes": cint(q.plants_from_order),
			"plants_short": cint(q.plants_short),
			"cuttings_short": cint(q.cuttings_short),
			"weeks_short": cint(q.weeks_short),
			"covers": cint(q.order_covers_season),
			"order_by": str(est.order_by_date or "") if est else str(q.first_order_by or ""),
			"order_late": bool(cint(q.orders_late)),
			"propagates_here": cint(q.propagates_here),
			"in_house": q.in_house_stages,
			"basis": q.sizing_basis,
			"cutting_weeks": (max(0, cint(v.motherstock_life_weeks)
			                      - cint(v.weeks_tc_to_first_cut())
			                      * max(0, cycles - 1))
			                  if cint(v.motherstock_life_weeks) else None),
		},
		"demand": {
			"asked": cint(p.total_demand_stems),
			"planned": stems,
			"coverage_pct": flt(p.coverage_pct),
			"weeks_in_deficit": cint(p.weeks_in_deficit),
			"at_risk": at_risk,
			"risk_exceeds_plan": risk_exceeds_plan,
			"risk_note": (_("{0} covers the whole of {1} for this season, and it is "
			                "sized on more than this plan grows. What is short here "
			                "is at most everything this plan was going to cut."
			                ).format(prop.name, q.variety)
			              if risk_exceeds_plan and prop else None),
			"after_risk_pct": (round(max(0, stems - at_risk)
			                         / cint(p.total_demand_stems) * 100, 1)
			                   if cint(p.total_demand_stems) else None),
		},
		"ground": {
			"plants": cint(q.total_plants_at_field),
			"beds": cint(q.beds_required),
			"beds_available": cint(q.beds_available),
			"capped": cint(q.capped_by_space),
			"plantings": len(rows),
		},
		"when": {
			"first_sticking": str(prop.first_sticking_date or "") if prop else "",
			"first_planting": min(plantings) if plantings else "",
			"last_planting": max(plantings) if plantings else "",
			"first_harvest_week": min(firsts) if firsts else None,
			"last_harvest_week": max(firsts) if firsts else None,
		},
		"propagation": (dict(prop) if prop else None),
		"raised_by": [{
			"type": r.alloc_type or ("Outside Propagator" if r.supplier else "Own Unit"),
			"party": r.party or r.supplier or r.propagation_unit,
			"share_pct": flt(r.share_pct),
			"plants": cint(r.plants),
			"rate": flt(r.rate_per_plant),
			"amount": flt(r.amount),
			"lead_weeks": cint(r.lead_weeks),
			"start_by": str(r.start_by or ""),
			"plants_from": str(r.plants_from or ""),
			"plants_to": str(r.plants_to or ""),
			"basis": r.ready_basis,
			"note": r.note,
			"propagation_plan": r.propagation_plan,
			"purchase_order": r.purchase_order,
		} for r in (q.propagation_allocation or [])],
		"arrivals": arrivals,
	}


@frappe.whitelist()
def tc_options(production_plan, cycles=None, tc_qty=None):
	"""What to buy at the pool's entry stage, and what each cycle count costs.

	Only a route that passes a standing stage has any of this to say. A crop
	bought as rooted plants buys one per plant and there is nothing to choose; a
	crop raised through a motherstock is bought once, multiplied if the farm wants
	to, and cut from until the line expires -- and every extra multiplication is
	fewer plantlets bought and more of the line's life spent buying them.
	"""
	p = frappe.get_doc("Summer Flower Production Plan", production_plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	from upande_summer_flowers.summer_flowers import sourcing
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_motherstock_batch.summer_flower_motherstock_batch import (
			tc_order_by_date,
		)

	decided = sourcing.route_plan(v)
	entry = decided.get("entry_stage")
	if not entry or not sourcing.standing_stage(v, entry):
		return {"standing": False, "reason": decided.get("reason")}

	# The busiest sticking week is what the pool is sized for: cuttings cannot be
	# banked, so the weeks either side lend it nothing. The plants figure is the
	# plan's own, already rounded to whole minimum planting areas.
	weekly = cint(p.peak_weekly_sticking_planned)
	first = None
	sticks = [(cint(b.sticking_year), cint(b.sticking_week)) for b in p.plan_blocks
	          if b.is_new_planting and cint(b.sticking_year)]
	if sticks:
		y, w = min(sticks)
		first = iso_monday(y, w)

	protocol_cycles = cint(v.max_multiplication_cycles)
	chosen = cint(cycles) if cycles not in (None, "") else protocol_cycles
	life = cint(v.motherstock_life_weeks)
	est = cint(v.weeks_tc_to_first_cut())

	rows = []
	for c in range(1, max(6, protocol_cycles + 2)):
		r = sourcing.requirement(v, entry, cint(p.new_plants_required), weekly,
		                         cycles=c)
		if not r:
			continue
		# Every cycle is an establishment, so the order goes in that much earlier
		# and the line has that much less left to cut.
		rows.append({
			"cycles": c,
			"factor": round(v.multiplication_factor(c), 2),
			"pool": cint(r.get("pool")),
			"units": cint(r.get("calculated_units")),
			"order_by": str(tc_order_by_date(v, first, cycles=c) or "") if first else "",
			"cutting_weeks": (max(0, life - est * max(0, c - 1)) if life and est
			                  else None),
			"is_protocol": c == protocol_cycles,
		})

	now = sourcing.requirement(v, entry, cint(p.new_plants_required), weekly,
	                           cycles=chosen, units_override=tc_qty)
	order_by = tc_order_by_date(v, first, cycles=chosen) if first else None
	return {
		"standing": True,
		"plan": p.name, "variety": p.variety, "farm": p.farm, "protocol": v.name,
		"entry_stage": entry,
		"standing_stage": now.get("standing_stage"),
		"peak_week": p.peak_sticking_week_planned,
		"peak_plants": weekly,
		"season_plants": cint(p.new_plants_required),
		"cuttings_per_plant": flt(v.cuttings_per_plant_required) or 1.0,
		"cuttings_per_mother_per_week": flt(v.cuttings_per_plant_per_week),
		"weekly_draw": cint(now.get("weekly_draw")),
		"standing_capacity": cint(now.get("standing_capacity")),
		"net_cuttings": cint(now.get("net_cuttings")),
		"pool": cint(now.get("pool")),
		"calculated": cint(now.get("calculated_units")),
		"units": cint(now.get("units")),
		"overridden": bool(now.get("overridden")),
		"cycles": chosen,
		"protocol_cycles": protocol_cycles,
		"order_by": str(order_by or ""),
		"order_late": bool(order_by and getdate(order_by) < getdate(nowdate())),
		"line_life_weeks": life,
		"cycle_cost_weeks": est,
		# The parts of that establishment, so the dialog can show the sum rather
		# than a number nobody can check.
		"weeks_on_tray": cint(v.weeks_on_tray),
		"weeks_on_pot": cint(v.weeks_on_pot),
		"ramp_weeks": cint(v.ramp_weeks) or cint(v.weeks_to_max_pc),
		"hardening_weeks": cint(v.hardening_weeks),
		"counts_ramp": bool(cint(v.establishment_includes_ramp)),
		"counts_hardening": bool(cint(v.establishment_includes_hardening)),
		"cutting_weeks": (max(0, life - est * max(0, chosen - 1)) if life and est
		                  else None),
		"blocked": now.get("blocked"),
		"basis": now.get("basis"),
		"by_cycles": rows,
	}


@frappe.whitelist()
def build(production_plan, method=None, entry_stage=None, supplier=None,
          fit_to_space=1, cycles=None, tc_qty=None, replace=0, divert_weeks=None,
          units_qty=None):
	"""Turn a plan's plantings into one requirement line each, and price the order.

	`divert_weeks` is how the order is sized on a route through a motherstock:
	weeks from the first cutting in which the whole cut goes back as new mothers.
	`cycles` is the figure that used to do that job and is kept only so an older
	caller does not fail; it no longer sizes anything.
	"""
	fit_to_space = cint(fit_to_space)
	p = frappe.get_doc("Summer Flower Production Plan", production_plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	from upande_summer_flowers.summer_flowers import sourcing
	from upande_summer_flowers.summer_flowers.doctype \
		.summer_flower_production_plan.summer_flower_production_plan import (
			plants_per_bed_for,
		)

	# A plan that already sources this one is revised, not replaced. It used to be
	# deleted and a new one written in its place, behind a warning and a
	# confirmation -- which cost the reader two decisions to get to the only thing
	# they wanted, and took the document's number, its comments and its history
	# with it every time a number changed.
	#
	# The one case that is still refused is a submitted plan whose order date has
	# come round: the material is on its way, and rewriting the order then buys it
	# twice. That is not a warning about tidiness, it is money.
	have = existing_for(p.name)
	revising = None
	if have:
		if have["docstatus"] == 1 and have["order_date_passed"]:
			frappe.throw(have["why_not"], title=_("The order has already gone out"))
		if have["docstatus"] == 1:
			# Submitted but nothing ordered yet. Frappe's own way to revise a
			# submitted document is to cancel it and amend, which keeps the chain
			# rather than pretending the first one never happened.
			old_doc = frappe.get_doc("Summer Flower Procurement Plan", have["name"])
			old_doc.flags.ignore_permissions = True
			old_doc.cancel()
			revising = have["name"]
		else:
			revising = have["name"]

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
	chosen_stage = bool(entry_stage)
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

	# A crop bought two ways is bought BOTH ways, in the shares the route gives.
	# Eryngium is 65% tissue culture and 35% roots, and the route has said so since
	# it was written -- but the order was placed along one stage for the whole
	# season, so the farm was told to buy TC for plants it had decided to buy as
	# roots, and no roots at all. The two differ by 37 weeks of lead and by what one
	# unit becomes, so it is not a rounding difference: it is the wrong order.
	#
	# A stage named by the caller still takes the whole order. That is a person
	# overruling the route on purpose, and the share is not theirs to redistribute.
	ways = []
	if method == "Purchase" and not chosen_stage:
		for w in (decided.get("ways_in") or []):
			wj = sourcing.journey(v, w["stage"])
			if not wj or flt(w.get("share_pct")) <= 0:
				continue
			ways.append({
				"stage": w["stage"],
				"share_pct": flt(w["share_pct"]),
				"per_unit": flt(wj["plants_per_unit"]) or 1.0,
				"notice_weeks": cint(wj["weeks_to_ground"]) + cint(wj["lead_weeks"]),
			})
	if len(ways) < 2:
		ways = [{"stage": entry_stage, "share_pct": 100.0, "per_unit": per_unit,
		         "notice_weeks": weeks_to_ground + lead}]
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
	                             peak_date=peak_monday, cycles=cycles,
	                             units_override=tc_qty)
	        if entry_stage else None)
	standing = bool(need and need.get("kind") == "standing")
	if standing and need.get("blocked"):
		frappe.throw(need["blocked"], title=_("The order cannot be sized"))

	# The order through a motherstock is sized by the week-by-week cut, not by a
	# multiplication factor. A mother cuts every week of its life, so generations
	# overlap a week apart rather than queueing an establishment apart, and the old
	# sum worked the order date back from a pool that lands after the block is
	# cleared -- 60 weeks of build-up against a 52-week line.
	solved = None
	if standing:
		from upande_summer_flowers.summer_flowers import propagation_solver as psol

		# The motherstock plan decides, this document buys. Sizing used to happen
		# here and the line was written afterwards, which put the decision after
		# the purchase order that was supposed to follow from it. If a line has
		# already been agreed for this production plan, its figures are the ones
		# ordered -- unless the caller passed something explicitly, which is a
		# person overruling it on purpose.
		agreed = frappe.db.get_value(
			"Summer Flower Motherstock Plan", {"production_plan": p.name},
			["name", "tc_to_order", "multiplications"], as_dict=True)
		# Taken as a pair or not at all: a caller who names one lever has the
		# other re-solved for it, because the agreed quantity was worked out for
		# the agreed multiplication and means nothing beside a different one.
		if agreed and cycles in (None, "") and not cint(tc_qty):
			cycles = cint(agreed.multiplications)
			tc_qty = cint(agreed.tc_to_order)
		if agreed:
			need["agreed_from"] = agreed.name

		# Sized by the busiest week. The pool is bought once and cut from every
		# week of the season, so the week that sets its size is the busiest one --
		# and the multiplication is worked out here rather than asked for, because
		# what it costs is time and nobody should have to do that arithmetic.
		sizing = psol.peak_sizing(p, cycles=cycles)
		if not sizing:
			frappe.throw(
				_("This plan has no sticking weeks, so there is no peak week to size "
				  "the order from."), title=_("The order cannot be sized"))
		pick = sizing["pick"]
		need["cycles"] = cint(pick["cycles"])
		need["units"] = cint(pick["buy"])
		need["calculated_units"] = cint(sizing["options"][sizing["suggested"]]["buy"])
		need["overridden"] = cint(tc_qty) and cint(tc_qty) != cint(pick["buy"])
		if cint(tc_qty):
			need["units"] = cint(tc_qty)
		# Sized from the quantity actually ordered, not from the one the sum
		# suggested. Buying fewer plantlets buys a smaller pool, a smaller pool
		# cuts less every week, and the weeks it cannot cover are a shortfall --
		# which used to go unsaid, because the pool, the weekly draw and the
		# dates were all read off the suggestion whatever was bought instead.
		sched = psol.peak_schedule(p, sizing, cycles=need["cycles"],
		                           tc=need["units"])
		if sched:
			need["pool"] = cint(sched.get("peak_pool"))
			need["order_by_date"] = sched.get("order_by") or pick["order_by"]
			need["to_field"] = cint(sched.get("to_field"))
			need["cuttings_short"] = cint(sched.get("shortfall"))
			need["weeks_short"] = max(0, cint(sched.get("weeks"))
			                          - cint(sched.get("weeks_met")))
			need["covers"] = 1 if sched.get("covers") else 0
		else:
			need["pool"] = cint(pick["pool"])
			need["order_by_date"] = pick["order_by"]
		need["peak"] = sizing
		need["basis"] = _(
			"The busiest sticking week is {0}, which needs {1} cuttings -- and that "
			"is what the pool is sized for, because cuttings cannot be banked. At "
			"{2} cuttings a mother a week that is {3} mother plants, divided by {4} "
			"generations standing, rounded up to a whole planting area of {5}: "
			"{6} plantlets. Ordered by {7} so the pool is full for the first "
			"sticking on {8}, and the line has {9} cutting weeks left for {10} "
			"sticking weeks.").format(
			sizing["peak_week_label"], "{:,}".format(cint(sizing["peak_cuttings"])),
			flt(sizing["cuttings_per_mother_per_week"]),
			"{:,}".format(int(round(cint(sizing["peak_cuttings"])
			                        / flt(sizing["cuttings_per_mother_per_week"])))),
			pick["generations"], "{:,}".format(cint(sizing["min_planting_area"])),
			"{:,}".format(cint(pick["buy"])), pick["order_by"],
			sizing["first_sticking"], cint(pick["cutting_weeks"]),
			cint(sizing["sticking_weeks"]))
		if not pick["covers_season"]:
			need["basis"] += " " + _(
				"That is {0} cutting weeks short of the season; {1} multiplication(s) "
				"is the most the line can carry.").format(
				cint(sizing["sticking_weeks"]) - cint(pick["cutting_weeks"]),
				sizing["suggested"])
	if need and not standing and not per_unit:
		frappe.throw(
			_("Nothing survives the route from {0} as the protocol has it, so no order "
			  "can be sized from it.").format(entry_stage),
			title=_("The order cannot be sized"))

	amended_from = None
	if revising:
		prev = frappe.get_doc("Summer Flower Procurement Plan", revising)
		if prev.docstatus == 2:
			# Cancelled a moment ago: amend it, so the new one carries its number
			# forward and the old one is still there to read.
			amended_from = prev.name
			doc = frappe.new_doc("Summer Flower Procurement Plan")
		else:
			# A draft is just rewritten. Same document, same name, same comments.
			doc = prev
			for table in ("requirements",):
				doc.set(table, [])
	else:
		doc = frappe.new_doc("Summer Flower Procurement Plan")
	doc.amended_from = amended_from
	doc.production_plan = p.name
	doc.variety, doc.farm, doc.company = p.variety, p.farm, p.company
	doc.season, doc.protocol = p.get("season"), v.name
	doc.method = method
	doc.entry_stage = (
		" + ".join("%s %g%%" % (w["stage"], w["share_pct"]) for w in ways)
		if (method == "Purchase" and len(ways) > 1)
		else (entry_stage if method == "Purchase" else None))
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
	built = []
	# A procurement plan is the purchase and nothing else. Where the material is
	# bought once and propagated -- tissue culture into a motherstock -- there is
	# one thing to buy, and the cohorts are not it: when each planting gets its
	# plants is the propagation plan's answer, off the pool that purchase built.
	# Listing all thirty-eight here with nothing to order against them said the
	# farm was buying something for each of them, in a column of zeros.
	for b in ([] if standing else rows):
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
		required = getdate(b.planting_date) if b.planting_date else None
		# One line per way in. The cohort's plants are split in the route's shares,
		# the last way taking the remainder so the lines still add to the cohort --
		# a rounded share dropped plants that nobody then bought.
		taken = 0
		for i, way in enumerate(ways):
			share = (plants - taken) if i == len(ways) - 1 else int(
				round(plants * way["share_pct"] / 100.0))
			taken += share
			# On a standing route the cohort buys nothing: it is stuck with cuttings
			# off the pool, and the pool was bought once on the establishment line
			# below.
			units = 0 if standing else (
				int(math.ceil(share / way["per_unit"]))
				if (share and way["per_unit"]) else 0)
			order_by = None if standing else (
				required - datetime.timedelta(weeks=way["notice_weeks"])
				if required else None)
			line = doc.append("requirements", {
				"line_type": "Planting",
				"drawn_from_pool": 1 if standing else 0,
				"cohort": b.name,
				"planting_week": ("%s-W%02d" % (cint(b.planting_year),
				                                cint(b.planting_week))
				                  if b.planting_year else None),
				"planting_date": b.planting_date,
				"first_harvest_week": ("%s-W%02d" % (cint(b.first_harvest_year),
				                                     cint(b.first_harvest_week))
				                       if b.first_harvest_year else None),
				"qty_at_field": share,
				# Beds are the cohort's, not the share's: the two ways in fill the
				# same ground. Counting them on every line would book the block twice.
				"beds": beds if i == 0 else 0,
				"capped": capped,
				"method": method,
				"entry_stage": way["stage"],
				"supplier": doc.supplier,
				"qty_to_order": units,
				"required_at_site_date": required,
				"order_by_date": order_by,
				# What the plan is asking for. A supplier may come back with something
				# else, and that goes in confirmed_delivery_week -- the two are not the
				# same fact and the calendar keys on the second.
				"expected_delivery_date": required,
			})
			built.append((line, way["per_unit"]))

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
			"order_by_date": (need.get("order_by_date")
			                  or tc_order_by_date(v, first_stick,
			                                     cycles=cint(need.get("cycles")))),
			"expected_delivery_date": first_stick,
			"notes": need.get("basis"),
		})
		doc.pool_plants = cint(need.get("pool"))
		doc.weekly_draw = cint(need.get("weekly_draw"))
		szg = need.get("peak") or {}
		pk = szg.get("pick") or {}
		doc.peak_cuttings = cint(szg.get("peak_cuttings"))
		doc.peak_week = szg.get("peak_week_label")
		doc.generations = flt(pk.get("generations"))
		doc.cutting_weeks_left = cint(pk.get("cutting_weeks"))
		doc.cycles_suggested = cint(szg.get("suggested"))
		doc.multiplication_cycles = cint(pk.get("cycles"))
		doc.divert_weeks = cint(need.get("divert_weeks"))
		# The line itself is written down separately. This document says what to
		# buy; what happens to it week by week after it lands is the motherstock
		# plan's job, and the propagation plan is built from that.
		doc.flags.write_motherstock_plan = True
		doc.calculated_units = cint(need.get("calculated_units"))
		doc.units_overridden = 1 if need.get("overridden") else 0
		doc.plants_from_order = cint(need.get("to_field"))
		doc.cuttings_short = cint(need.get("cuttings_short"))
		doc.weeks_short = cint(need.get("weeks_short"))
		doc.order_covers_season = cint(need.get("covers"))
		# The same question the direct route answers, asked of the pool: what the
		# plan wanted standing, less what this order actually sends to the field.
		doc.plants_short = max(0, cint(p.new_plants_required)
		                       - cint(need.get("to_field")))
		doc.sizing_basis = (need.get("basis") or "") + (
			_(" Ordered {0} instead of the {1} the sum asks for.").format(
				"{:,}".format(cint(need.get("units"))),
				"{:,}".format(cint(need.get("calculated_units"))))
			if need.get("overridden") else "") + (
			_(" At {0} plantlets the line stands {1} mothers at its peak and "
			  "sends {2} plants to the field, which is {3} cuttings short across "
			  "{4} of {5} sticking weeks. The demand is not reduced by this.")
			.format("{:,}".format(cint(need.get("units"))),
			        "{:,}".format(cint(need.get("pool"))),
			        "{:,}".format(cint(need.get("to_field"))),
			        "{:,}".format(cint(need.get("cuttings_short"))),
			        cint(need.get("weeks_short")),
			        cint(need.get("weeks_short"))
			        + cint((sched or {}).get("weeks_met")))
			if cint(need.get("cuttings_short")) else "")

	# A hand-set order on a route that buys one unit per plant. The pool route
	# answers this in the solver above; here there is no pool, so the answer is
	# simply which plantings the order reaches.
	if not standing:
		planted = sum(cint(line.qty_at_field) for line, _pu in built)
		doc.calculated_units = sum(cint(line.qty_to_order) for line, _pu in built)
		if cint(units_qty) and cint(units_qty) < doc.calculated_units:
			cut = trim_to_order(built, cint(units_qty), ppb)
			doc.units_overridden = 1
			doc.plants_from_order = cint(cut["made"])
			doc.plants_short = cint(cut["short"])
			doc.weeks_short = cint(cut["cohorts_short"])
			doc.order_covers_season = 0
			doc.sizing_basis = _(
				"Ordered {0} instead of the {1} the plantings ask for. That plants "
				"{2} of {3}, so {4} plants across {5} planting(s) have nothing to "
				"put in the ground. Served in planting order, so the shortfall "
				"falls at the end of the season. The demand is not reduced by this."
			).format("{:,}".format(cint(units_qty)),
			         "{:,}".format(cint(doc.calculated_units)),
			         "{:,}".format(cint(cut["made"])), "{:,}".format(cint(cut["wanted"])),
			         "{:,}".format(cint(cut["short"])), cint(cut["cohorts_short"]))
		else:
			doc.units_overridden = 0
			doc.plants_from_order = planted
			doc.plants_short = 0
			doc.weeks_short = 0
			doc.order_covers_season = 1

	wanted_beds = sum(cint(b.beds) for b in rows)
	if standing:
		# Nothing was trimmed because nothing per-cohort was written; the ground
		# question belongs to the plantings, which the propagation plan schedules.
		trimmed = 0
	if trimmed:
		short = wanted_beds - beds_available
		doc.space_note = _(
			"Not enough ground. The plan needs {0} beds and {1} has {2}, so it is "
			"{3} beds short and {4} of {5} plantings were trimmed. The demand is not "
			"reduced by this -- it still asks for {0} beds. Either find more land, "
			"or accept that this much of the season cannot be grown here."
		).format(wanted_beds, p.farm, beds_available, short, trimmed, len(rows))
	# Re-planning writes the order before anyone has been offered the units to
	# raise it, so the allocation cannot be demanded on this save -- only on the
	# next one, when it is a person saving rather than the planner.
	doc.flags.rebuilding = True
	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.flags.ignore_permissions = True
		doc.save()

	# The line, written down beside the order. This document says what to buy; the
	# motherstock plan says what happens to it week by week once it lands, and the
	# propagation plan is built from that rather than from here.
	if doc.flags.get("write_motherstock_plan"):
		from upande_summer_flowers.summer_flowers.doctype \
			.summer_flower_motherstock_plan.summer_flower_motherstock_plan import (
				for_plan,
			)
		try:
			doc.db_set("motherstock_plan",
			           for_plan(production_plan,
			                    tc_to_order=cint(need.get("units")),
			                    multiplications=cint(need.get("cycles"))),
			           update_modified=False)
		except Exception:
			# The order is the thing that must survive. A line that cannot be
			# worked out is worth a message, not a lost procurement plan.
			frappe.log_error(frappe.get_traceback(),
			                 "Motherstock plan for %s" % doc.name)
			frappe.msgprint(
				_("The order was saved, but the week-by-week motherstock line could "
				  "not be worked out. Open the production plan and press Plan "
				  "Procurement again to see why."),
				indicator="orange", title=_("No motherstock line"))

	if doc.space_note:
		frappe.msgprint(doc.space_note, indicator="orange",
		                title=_("Not enough ground"))
	return doc.name
