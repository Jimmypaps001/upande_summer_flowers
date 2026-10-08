# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""How a Production Plan's cuttings get sourced.

The Production Plan says how many plants go in the ground in which week. The
Motherstock Batch already knows how to turn a peak weekly cutting requirement
into a TC order, a multiplication schedule and a cost. This is the layer between
them: the week-by-week requirement, what existing motherstock can already cover,
and therefore what actually has to be bought.

Sizing is on the peak week, not the annual total, because a cutting cannot be
banked -- it is stuck the week it is taken. A pool big enough for the year but
not for its busiest week fails in that week.
"""

import math

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt, getdate, now_datetime

from upande_summer_flowers.summer_flowers.doctype \
	.summer_flower_motherstock_batch.summer_flower_motherstock_batch import (
		lookup_tc_rate,
	)
from upande_summer_flowers.summer_flowers.lifecycle_sim import ramp_ratio
from upande_summer_flowers.summer_flowers.planning import iso_monday


def _back(week, weeks):
	"""The week material had to be set down to be plants by `week`."""
	if not week:
		return None
	return add_days(getdate(week), -7 * cint(weeks))


def _iso(d):
	y, w, _dow = getdate(d).isocalendar()
	return "%d-W%02d" % (y, w)

APPROVER_ROLES = ("Farm Manager", "Agriculture Manager", "System Manager")


def _has_role(roles=APPROVER_ROLES):
	return bool(set(roles) & set(frappe.get_roles(frappe.session.user)))


class SummerFlowerPropagationPlan(Document):
	def validate(self):
		self.pull_header()
		self.check_one_per_season()
		self.count_outputs()
		self.note_protocol_provenance()
		self.build_requirement()
		self.apply_existing_motherstock()
		self.apply_motherstock_plan()
		self.roll_up()

	# --------------------------------------------------------------- header
	# The figures a rebuild is judged on. Not everything -- dates and warnings follow
	# from these, and listing them all would bury the numbers that matter.
	TRACKED = (
		("total_plants_to_stick", "Plants to stick"),
		("total_cuttings_required", "Cuttings required"),
		("peak_weekly_cuttings", "Peak weekly cuttings"),
		("mother_plants_required", "Mother plants"),
		("tc_plants_required", "TC to order"),
		("cuttings_uncovered", "Cuttings not covered"),
		("weeks_sticking", "Sticking weeks"),
		("tc_order_date", "Order TC by"),
		("first_sticking_date", "First sticking"),
	)

	def snapshot(self):
		"""The tracked figures as they stand, for comparing across a rebuild."""
		return {f: self.get(f) for f, _label in self.TRACKED}

	def describe_changes(self, before):
		"""What moved, in the farm's terms rather than field names."""
		lines = []
		for field, label in self.TRACKED:
			was, now = before.get(field), self.get(field)
			if (was or 0) == (now or 0) or str(was or "") == str(now or ""):
				continue
			fmt = (lambda x: "{:,}".format(cint(x))) if not str(field).endswith("date") \
				else (lambda x: str(x or "not set"))
			lines.append(_("{0}: {1} to {2}").format(label, fmt(was), fmt(now)))
		return lines

	def check_one_per_season(self):
		"""One propagation plan per variety per season, and only one.

		Propagation is a single physical operation for a crop in a year: one pool of
		motherstock, one bench, one TC order. Two plans for the same variety and season
		would each size that pool as though the other did not exist, and the farm would
		have no way to tell which order to place.
		"""
		if not (self.variety and self.season_start_year):
			return
		filters = {"variety": self.variety, "season_start_year": self.season_start_year,
		           "status": ["!=", "Rejected"]}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		dupe = frappe.db.get_value("Summer Flower Propagation Plan", filters, "name")
		if dupe:
			frappe.throw(_(
				"{0} is already the propagation plan for {1} in season {2}. Rebuild it "
				"from the production plan rather than creating a second one -- one "
				"variety in one season has one pool of motherstock and one TC order."
			).format(dupe, self.variety, self.season), title=_("Plan exists"))

	def pull_header(self):
		p = frappe.get_cached_doc("Summer Flower Production Plan",
		                          self.production_plan)
		self.market_demand = p.market_demand
		self.variety = p.variety
		self.farm = p.farm
		self.protocol = p.protocol
		self.company = p.company
		self.currency = p.currency
		# The season comes from the production plan, so the two cannot disagree about
		# which year is being propagated for.
		self.season_start_year = cint(p.season_start_year)
		self.season = p.season
		self._plan = p
		self._version = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
		# A pool cuts its way up to full capacity; it does not arrive there. Every
		# weekly figure below is scaled by this, which is what the protocol's
		# ramp_profile has always meant and what nothing outside the simulator used.
		self._ramp = self._version.ramp_ratios()

	def count_outputs(self):
		"""Count what exists, rather than remembering what was made.

		These two were incremented when a batch or a request was created, so
		deleting one left the count claiming it was still there -- and the process
		overview then reported the TC order as placed while the motherstock stage
		said no batch had been raised.
		"""
		self.motherstock_batches_created = frappe.db.count(
			"Summer Flower Motherstock Batch",
			{"production_plan": self.production_plan, "docstatus": ["<", 2]})
		if frappe.db.exists("DocType", "Seedling Request"):
			self.seedling_requests_created = frappe.db.count(
				"Seedling Request", {"custom_propagation_plan": self.name})

	def note_protocol_provenance(self):
		"""Every header field here is the production plan's answer, not a choice.

		The protocol decides cuttings per plant, the multiplication rate and the TC
		lead time, so a propagation plan built on a different version than its
		production plan would size the motherstock for a crop nobody is planting.
		It is fetched, never set -- and if the plan itself reports the version moved
		under it, that is said here rather than buried on the other document.
		"""
		p = self._plan
		v = self._version
		notes = [_("Protocol {0} (v{1}, {2}) comes from production plan {3}.")
		         .format(v.name, v.version, v.version_status, p.name)]
		if cint(p.get("protocol_stale")):
			notes.append("")
			notes.append(_("That plan reports its protocol changed since it was "
			               "built, so these numbers inherit the same gap:"))
			notes.append(p.protocol_note or "")
		self.protocol_note = "\n".join(x for x in notes if x is not None)

	# ---------------------------------------------------------- requirement
	def build_requirement(self):
		"""Plants to stick per week, scaled up into cuttings.

		Taken from the plan's proposed plantings, which already carry the sticking
		week the protocol implies, so the two documents cannot disagree about when
		a planting has to be started.
		"""
		v = self._version
		# One definition of "cuttings for these plants", the protocol's own, which
		# applies the rooting and field losses and then the cutting reject rate on
		# top. This used to take cuttings_per_plant_required raw while the planning
		# API used cuttings_for_plants(), so the two documents quoted different
		# cutting counts for the same plantings -- and the pool was sized off the
		# smaller one, short by the reject rate.
		by_week = {}
		for b in self._plan.plan_blocks:
			if not cint(b.is_new_planting):
				continue
			y, w = cint(b.sticking_year), cint(b.sticking_week)
			if not (y and w):
				continue
			slot = by_week.setdefault((y, w), {"plants": 0, "out": set(), "no_block": 0})
			slot["plants"] += cint(b.plants)
			# Counted, not skipped. Skipping them was defensible on its own terms --
			# a planting with no block will not happen, so why raise cuttings for it
			# -- but it broke the chain in two places. The TC order is sized on every
			# proposed planting, because it goes to the lab months before anyone knows
			# which block will be free; and the dashboard defers to this document once
			# it exists, so a propagation plan built from a plan with nothing placed
			# replaced a 10,046 plantlet order with zero. One basis, and the count of
			# plantings still needing a block said out loud.
			if cint(b.get("not_placed")):
				slot["no_block"] += cint(b.plants)
			if b.planting_year and b.planting_week:
				slot["out"].add("%s-W%02d" % (b.planting_year, cint(b.planting_week)))
				# The day the field wants them. A sticking week says when the
				# cuttings go in the tray; it never said when the plants leave the
				# unit, which is the date the farm actually works to.
				out_on = iso_monday(cint(b.planting_year), cint(b.planting_week))
				if not slot.get("deliver") or out_on < slot["deliver"]:
					slot["deliver"] = out_on

		self.set("weeks", [])
		for (y, w) in sorted(by_week):
			slot = by_week[(y, w)]
			self.append("weeks", {
				"year": y, "week_no": w, "week_start_date": iso_monday(y, w),
				"plants_to_stick": slot["plants"],
				"cuttings_required": v.cuttings_for_plants(slot["plants"]),
				"plant_week": ", ".join(sorted(slot["out"]))[:140],
				"deliver_on": slot.get("deliver"),
				"notes": _("{0} of these plants have no block yet").format(
					slot["no_block"]) if slot["no_block"] else None,
			})

		plants = sum(cint(r.plants_to_stick) for r in self.weeks)
		cuttings = sum(cint(r.cuttings_required) for r in self.weeks)
		self._plants_without_block = sum(
			slot["no_block"] for slot in by_week.values())
		self.cuttings_per_plant = (cuttings / plants) if plants else (
			flt(v.cuttings_per_plant_required) or 1.0)

	# ------------------------------------------------- existing motherstock
	def apply_existing_motherstock(self):
		"""Net off what standing motherstock can already cut in each week.

		A batch only counts between its first sticking date and its expiry: a pool
		that has not established yet, or is past its renewal, cannot supply the
		week being planned.
		"""
		v = self._version
		ramp = self._ramp
		per_week = flt(v.cuttings_per_plant_per_week) or 1.0
		# Only a batch the farm has actually committed to. A draft is a working
		# document and "Planned" is an intention -- counting one as standing
		# motherstock credited this plan with 34,223 mother plants and 876,000
		# cuttings off a pool nobody had built, and reported 236% cover against a
		# requirement it could not meet at all.
		batches = frappe.get_all(
			"Summer Flower Motherstock Batch",
			filters={"variety": self.variety, "farm": self.farm,
			         "docstatus": 1,
			         "batch_status": ["!=", "Expired"]},
			fields=["name", "mother_plants", "first_sticking_date", "expiry_date",
			        "batch_status"])
		# What was left out, and why, so a cover figure that just fell does not look
		# like the arithmetic breaking.
		ignored = frappe.get_all(
			"Summer Flower Motherstock Batch",
			filters={"variety": self.variety, "farm": self.farm,
			         "docstatus": 0},
			fields=["name", "mother_plants", "batch_status"])
		self.motherstock_ignored_note = (_(
			"{0} batch(es) are not counted as standing: {1}. A draft is a working "
			"document, not a pool the farm can cut from. Submit it and it counts."
		).format(len(ignored), ", ".join(
			"%s (%s, %s mother plants)" % (b.name, b.batch_status,
			                               "{:,}".format(cint(b.mother_plants)))
			for b in ignored[:4])) if ignored else None)
		self.set("sources", [])
		# Full capacity per sticking week, accumulated across every pool. ramp_pct is
		# only meaningful against this, and a plan with no standing motherstock has
		# no existing capacity to measure against -- which is why it read 0%.
		self._at_full = {}
		pools = []
		for b in batches:
			if not (b.mother_plants and b.first_sticking_date):
				continue
			cap = int(round(cint(b.mother_plants) * per_week))
			pools.append({
				"cap": cap, "from": getdate(b.first_sticking_date),
				"to": getdate(b.expiry_date) if b.expiry_date else None,
			})
			self.append("sources", {
				"source_type": "Existing Motherstock",
				"motherstock_batch": b.name,
				"mother_plants": cint(b.mother_plants),
				"weekly_capacity": cap,
				"available_from": b.first_sticking_date,
				"available_to": b.expiry_date,
				"notes": _("Status {0}. {1} at full capacity from {2} after a "
				           "{3}-week ramp.").format(
					b.batch_status, cap,
					add_days(getdate(b.first_sticking_date), 7 * (len(ramp) - 1)),
					len(ramp)),
			})

		for r in self.weeks:
			monday = getdate(r.week_start_date)
			available = 0
			at_full = 0
			for p in pools:
				if monday < p["from"]:
					continue
				if p["to"] and monday > p["to"]:
					continue
				at_full += p["cap"]
				available += int(round(p["cap"] * ramp_ratio(
					(monday - p["from"]).days // 7, ramp)))
			covered = min(cint(r.cuttings_required), available)
			r.from_existing_ms = covered
			r.shortfall = cint(r.cuttings_required) - covered
			r.from_new_ms = 0
			r.capacity_available = available
			self._at_full[(r.year, r.week_no)] = at_full

	# ------------------------------------------------------ new motherstock
	def apply_motherstock_plan(self):
		"""Take the pool from the Motherstock Plan instead of sizing one here.

		This used to size its own pool, and it sized it from a single week: the
		worst uncovered week's cuttings, divided by cuttings per mother per week.
		That turns a flow into a stock -- 37,000 cuttings in the busiest week became
		37,000 mother plants -- and then divided the plantlet order by a
		multiplication factor, which is the sequential-generations model the farm
		does not work to. The two documents answered the same question differently:
		9,250 plantlets here against 28,000 on the procurement plan, and this one
		then reported 372,250 of 550,000 cuttings uncovered without changing its
		own answer.

		So it no longer answers it. The Motherstock Plan is where the line is
		decided -- how many plantlets, how many times the cut is sent back -- and
		this reads its week-by-week schedule for what the field can actually be
		given each week. One engine, one answer, and changing the decision there
		moves this document with it.
		"""
		self._peak_shortfall = 0
		self._first_needed = None
		self._ms_line = None
		self._at_full = getattr(self, "_at_full", {})
		self.ramp_weeks = len(self._ramp)

		ms = self._ms_line = self.motherstock_line()
		if not ms:
			# Nothing standing to cut from, which for a bought crop is the normal
			# case rather than a failure. Everything motherstock-shaped goes to
			# zero and the material bought takes its place: these plants arrive
			# as tissue culture or as roots, and the propagation unit hardens
			# them rather than raising them from a pool.
			self.mother_plants_required = 0
			self.peak_bench_sqm = 0
			self.full_capacity_date = None
			self.cuttings_lost_to_ramp = 0
			self.ramp_short_weeks = 0
			self.mother_plants_to_cover_ramp = 0
			self.new_pool_expiry = None
			self.new_pool_cutting_weeks = 0
			# Said here, in the branch that knows, rather than inferred later from
			# a missing line. A motherstock line can be missing because the crop
			# has none, or because building it failed; only this branch can tell
			# the two apart, and labelling the second as bought-in would silence
			# warnings a motherstock crop needs.
			self.raised_from = "Bought In"
			self.apply_bought_material()
			return

		self.raised_from = "Motherstock"
		v = self._version
		per_week = flt(v.cuttings_per_plant_per_week) or 1.0
		by_date = {getdate(w.week_start): w for w in ms.schedule}

		short = [r for r in self.weeks if cint(r.shortfall) > 0]
		if short:
			self._peak_shortfall = max(cint(r.shortfall) for r in short)
			self._first_needed = min(getdate(r.week_start_date) for r in short)

		lost, short_weeks = 0, 0
		for r in self.weeks:
			w = by_date.get(getdate(r.week_start_date))
			if not w:
				# Outside the line: before its first cut, or after the block is
				# cleared. Nothing can be taken, and the shortfall stands.
				continue
			# What the field can have is the week's cut less whatever went back as
			# mothers. In the diversion window that is nothing, which is the cost of
			# multiplying stated as a number rather than as a warning.
			cap = max(0, cint(w.cuttings_cut) - cint(w.to_multiplication))
			r.capacity_available = cint(r.capacity_available) + cap
			key = (r.year, r.week_no)
			self._at_full[key] = self._at_full.get(key, 0) + int(round(
				cint(w.mothers_standing) * per_week))
			if cint(r.shortfall) <= 0:
				continue
			take = min(cint(r.shortfall), cap)
			r.from_new_ms = cint(r.from_new_ms) + take
			r.shortfall = cint(r.shortfall) - take
			if cint(r.shortfall) and cint(w.to_multiplication):
				lost += cint(r.shortfall)
				short_weeks += 1

		self.mother_plants_required = cint(ms.peak_pool)
		self.peak_bench_sqm = round(cint(ms.peak_pool) / flt(v.plants_per_sqm_bench), 1) \
			if flt(v.plants_per_sqm_bench) else 0
		self.new_pool_expiry = ms.line_end_date
		gen1 = next((g for g in ms.generation_table if cint(g.generation) == 1), None)
		self.new_pool_cutting_weeks = cint(gen1.cutting_weeks) if gen1 else 0
		self.full_capacity_date = None
		full = next((w for w in ms.schedule
		             if cint(w.mothers_standing) and cint(w.cuttings_cut)
		             >= int(round(cint(w.mothers_standing) * per_week))), None)
		if full:
			self.full_capacity_date = full.week_start
		self.cuttings_lost_to_ramp = lost
		self.ramp_short_weeks = short_weeks
		self.mother_plants_to_cover_ramp = 0

		# One source row per generation, because that is what is standing. A single
		# row for "the new motherstock" could not say that gen 2 arrives over four
		# weeks and cuts for eleven weeks fewer than gen 1.
		for g in ms.generation_table:
			self.append("sources", {
				# The vocabulary stays as it was -- these are all the new pool. Which
				# generation is its own column, because "New Motherstock (TC)" four
				# times over says nothing about which of them cuts when.
				"source_type": "New Motherstock (TC)",
				"generation": cint(g.generation),
				"mother_plants": cint(g.mothers),
				"weekly_capacity": int(round(cint(g.mothers) * per_week)),
				"available_from": g.first_cut_date,
				"available_to": g.expiry_date,
				"notes": _("{0} mother plants arriving over {1} week(s) from {2}. "
				           "Cuts for {3} weeks: the whole block is cleared on {4}, "
				           "so a generation raised later gets fewer weeks rather "
				           "than a life of its own. From {5}.").format(
					"{:,}".format(cint(g.mothers)), cint(g.arrivals),
					g.stuck_date or g.first_cut_date, cint(g.cutting_weeks),
					g.expiry_date, ms.name),
			})

	def apply_bought_material(self):
		"""Source a crop that has no motherstock from what is bought for it.

		Leaving the sources empty said the propagation unit had nothing to work
		with, when in fact it has everything -- it is simply handed material
		rather than cutting it.

		It said worse than that. Every week came out uncovered, because cover
		meant cuttings off a mother plant and there are none, so the plan read
		1,036,704 cuttings short and 7,319,130 stems at risk for a season whose
		material is bought and scheduled. The weeks are covered here from what is
		actually coming, and the consignments behind that cover are written down
		so the unit has a list to work to: what arrives, from whom, in which
		week, and the week the field wants the plants.
		"""
		from upande_summer_flowers.summer_flowers import root_line as rl

		if not self.production_plan:
			return
		try:
			plan = frappe.get_doc("Summer Flower Production Plan",
			                      self.production_plan)
			m = rl.arrival_plan(plan)
		except Exception:
			frappe.clear_last_message()
			return
		if not m:
			return

		self.set("sources", [])
		sg = m.get("suggested") or {}
		if cint(sg.get("tc")):
			first = (m.get("tc_order_schedule") or [{}])[0].get("week")
			self.append("sources", {
				"source_type": "New Motherstock (TC)",
				"mother_plants": 0,
				"total_cuttings": cint(sg["tc"]),
				"available_from": first,
				"notes": _("Tissue culture, {0} weeks of lead and {1} in "
				           "propagation before it is plantable.").format(
					cint(m.get("tc_lead_weeks")), cint(m.get("tc_weeks_to_ground"))),
			})
		if cint(sg.get("roots")):
			# When the unit can be worked with, not when it was ordered. Roots
			# are one consignment a year ahead of use, so the order week is no
			# use to a propagation unit; the week they are in store is.
			ro = (m.get("root_order_schedule") or [{}])[0]
			first = ro.get("arrives") or ro.get("week")
			self.append("sources", {
				"source_type": "Bought-in Rooted Cuttings",
				"mother_plants": 0,
				"total_cuttings": cint(sg["roots"]),
				"available_from": first,
				"notes": _("Roots, {0} weeks of lead and {1} in propagation. One "
				           "root becomes {2} plants. They come in once and are "
				           "held in the coldroom, then drawn out week by week as "
				           "the plantings need them.").format(
					cint(m.get("root_lead_weeks")),
					cint(m.get("root_weeks_to_ground")),
					flt(m.get("returns_per_plant"))),
			})
		self.tc_plants_required = cint(sg.get("tc"))
		if m.get("tc_order_schedule"):
			self.tc_order_date = m["tc_order_schedule"][0]["week"]
		# The agreed order is the order. A suggestion is what to buy if nobody
		# has decided; once somebody has, the two must not differ by so much as
		# a plantlet or the card and this document quote different figures for
		# the same purchase.
		c = self._committed_choice()
		if c and cint(c.get("tc_plants_committed")):
			self.tc_plants_required = cint(c["tc_plants_committed"])
			if c.get("tc_order_date_committed"):
				self.tc_order_date = c["tc_order_date_committed"]
		self._cover_from_bought(m)

	def _cover_from_bought(self, m):
		"""Cover each week from the material arriving for it, and list it.

		The plan's own weekly rows already say how many plants each landing week
		takes from tissue culture and how many from roots. That IS the cover for
		a bought crop, so it is written against the week rather than left to read
		as a shortfall.
		"""
		from upande_summer_flowers.summer_flowers import tc_supply as ts

		by_plant_week = {}
		for w in (m.get("weeks") or []):
			by_plant_week[str(w.get("week"))] = w

		for r in self.weeks:
			w = by_plant_week.get(str(r.deliver_on))
			if not w:
				continue
			r.from_bought = cint(w.get("plants"))
			r.shortfall = max(0, cint(r.cuttings_required)
			                  - cint(r.from_existing_ms) - cint(r.from_new_ms)
			                  - cint(r.from_bought))

		self.set("intake", [])
		# Roots first: they are in store a year before any of it, and the unit
		# draws them out week by week.
		for pull in (m.get("root_withdrawals") or []):
			w = next((x for x in (m.get("weeks") or [])
			          if str(x.get("root_out_week")) == str(pull["week"])), None)
			self.append("intake", {
				"week_start_date": pull["week"], "week_iso": pull["week_iso"],
				"form": "Roots", "source": _("Cold store"),
				"reference": _("One consignment, drawn down"),
				"units": cint(pull["roots"]),
				"plants": cint((w or {}).get("by_roots")),
				"ready_on": (w or {}).get("week"),
				"ready_week": (w or {}).get("week_iso"),
				"deliver_on": (w or {}).get("week"),
				"plant_week": (w or {}).get("week_iso"),
			})

		# Then the tissue culture, consignment by consignment and lift by lift,
		# which is the only form in which a lab's deliveries are workable.
		plan = self._plan
		allocs = plan.get("tc_supplier_allocations")
		if allocs:
			try:
				batched = ts.batch_plan(plan, allocs,
				                        tc_target=cint(self.tc_plants_required))
			except Exception:
				frappe.clear_last_message()
				batched = None
			for h in ((batched or {}).get("handovers") or []):
				self.append("intake", {
					"week_start_date": _back(h["week"], m.get("tc_weeks_to_ground")),
					"week_iso": "", "form": "Tissue Culture",
					"source": h.get("supplier"),
					"reference": _("Batch {0}, lift {1} of {2}%").format(
						h.get("batch"), h.get("lift"), h.get("share_pct")),
					"units": cint(h["plants"]),
					"plants": cint(h["plants"]),
					"ready_on": h["week"], "ready_week": h["week_iso"],
					# The batcher has already worked out which planting week this
					# lift is for; recomputing it here is how two documents come
					# to disagree about one date.
					"deliver_on": h.get("to_farm"),
					"plant_week": h.get("to_farm_iso"),
				})
		elif cint(self.tc_plants_required):
			# Nothing allocated to a lab yet, so the best that can be said is the
			# week each landing takes its tissue culture from.
			for w in (m.get("weeks") or []):
				if not cint(w.get("by_tc")):
					continue
				self.append("intake", {
					"week_start_date": _back(w.get("week"),
					                         m.get("tc_weeks_to_ground")),
					"week_iso": "", "form": "Tissue Culture",
					"source": _("Not allocated to a lab yet"),
					"units": cint(w["by_tc"]), "plants": cint(w["by_tc"]),
					"ready_on": w.get("week"), "ready_week": w.get("week_iso"),
					"deliver_on": w.get("week"), "plant_week": w.get("week_iso"),
				})
		for row in self.intake:
			if row.week_start_date and not row.week_iso:
				row.week_iso = _iso(row.week_start_date)

	def motherstock_line(self):
		"""The Motherstock Plan behind this season, built if it is not there yet."""
		from upande_summer_flowers.summer_flowers.doctype \
			.summer_flower_motherstock_plan.summer_flower_motherstock_plan import (
				for_plan,
			)

		if not self.production_plan:
			return None
		name = frappe.db.get_value("Summer Flower Motherstock Plan",
		                           {"production_plan": self.production_plan}, "name")
		if not name:
			try:
				name = for_plan(self.production_plan)
			except Exception:
				# A variety that is not raised through a motherstock has no line, and
				# that is not a failure of this document.
				frappe.clear_last_message()
				return None
		return frappe.get_doc("Summer Flower Motherstock Plan", name)

	# --------------------------------------------------------------- totals
	def roll_up(self):
		full = getattr(self, "_at_full", {})
		for r in self.weeks:
			at_full = full.get((r.year, r.week_no), 0)
			r.ramp_pct = (cint(r.capacity_available) * 100.0 / at_full) if at_full else 0

		self.total_plants_to_stick = sum(cint(r.plants_to_stick) for r in self.weeks)
		self.total_cuttings_required = sum(cint(r.cuttings_required) for r in self.weeks)
		self.cuttings_from_existing = sum(cint(r.from_existing_ms) for r in self.weeks)
		self.cuttings_from_new = sum(cint(r.from_new_ms) for r in self.weeks)
		self.cuttings_from_bought = sum(cint(r.get("from_bought")) for r in self.weeks)
		self.cuttings_uncovered = sum(cint(r.shortfall) for r in self.weeks)
		self.weeks_sticking = len(self.weeks)
		if not self.raised_from:
			self.raised_from = "Motherstock" if self._ms_line else "Bought In"

		# Cuttings that cannot be taken are plants that cannot be stuck, and those
		# are stems the production plan is still counting. Reported here because the
		# plan cannot ask this document for it without the two becoming circular.
		per_plant = flt(self.cuttings_per_plant) or 1.0
		self.plants_short = int(self.cuttings_uncovered / per_plant) if per_plant else 0
		self.stems_at_risk = int(round(
			self.plants_short * flt(self._version.total_stems_per_plant_life)))
		self.existing_cover_pct = (
			self.cuttings_from_existing * 100.0 / self.total_cuttings_required
			if self.total_cuttings_required else 0)
		if self.raised_from == "Bought In":
			# A pool that does not exist has no mother plants, no build-up and no
			# bench. Carrying zeroes for them is harmless; carrying a shortfall
			# for them was not, because it read as a season that could not be
			# grown when the material for it is bought and scheduled.
			for f in ("mother_plants_required", "new_pool_cutting_weeks",
			          "peak_bench_sqm", "cuttings_lost_to_ramp",
			          "ramp_short_weeks", "mother_plants_to_cover_ramp",
			          "existing_cover_pct"):
				self.set(f, 0)
			self.motherstock_ignored_note = None
			self.new_pool_expiry = None
			self.full_capacity_date = None
		peak_row = max(self.weeks, key=lambda r: cint(r.cuttings_required),
		               default=None)
		self.peak_weekly_cuttings = cint(peak_row.cuttings_required) if peak_row else 0
		self.peak_week = ("%s-W%02d" % (peak_row.year, cint(peak_row.week_no))
		                  if peak_row else None)

		# The plantlet order is the Motherstock Plan's, not a second opinion. It used
		# to be sized here by running an unsaved Motherstock Batch through its own
		# controller -- which divided the pool by a multiplication factor and came
		# back with 9,250 plantlets against the procurement plan's 28,000 for the
		# same season.
		ms = self._ms_line
		if ms:
			self.tc_plants_required = cint(ms.tc_to_order)
			self.tc_order_date = ms.order_by_date
			self.tc_on_farm_date = ms.tc_arrival_date
			self.first_sticking_date = min(
				(getdate(r.week_start_date) for r in self.weeks), default=None)
			rate = flt(lookup_tc_rate(cint(ms.tc_to_order)))
			self.tc_cost = rate * cint(ms.tc_to_order)
			self.total_cost = flt(self.tc_cost)
			# The committed order is the order. The probe stays the sizing advice
			# beside it, which is what the batch's own override fields carry.
			c = self._committed_choice()
			if c:
				if cint(c.tc_plants_committed):
					self.tc_plants_required = cint(c.tc_plants_committed)
				if c.tc_order_date_committed:
					self.tc_order_date = c.tc_order_date_committed
		else:
			# No motherstock line is not no order. A bought crop still has tissue
			# culture to buy and a date to buy it by -- apply_bought_material has
			# already worked both out -- and zeroing them here threw that away
			# and left the plan reading as though nothing had to be ordered.
			if not cint(self.tc_plants_required):
				self.tc_order_date = None
			self.tc_cost = flt(lookup_tc_rate(cint(self.tc_plants_required))) * \
				cint(self.tc_plants_required)
			self.total_cost = flt(self.tc_cost)
			self.tc_on_farm_date = None
			self.first_sticking_date = None
		self.check_schedule()

	def check_schedule(self):
		"""Say plainly when the plan asks for cuttings that can no longer be grown.

		A production plan can propose a planting whose sticking week has already
		passed; the lead time back from that -- multiplication, establishment, lab
		turnaround -- then lands the TC order in the past. That is not a rounding
		nicety, it is weeks of production that cannot happen, so it is stated rather
		than left for someone to notice in the dates.
		"""
		today = getdate()
		notes = []
		# Sized on every proposed planting, including those with no block. Buying the
		# plantlets is a commitment to finding the blocks, so the number of plants
		# waiting on one belongs next to the order rather than three tabs away.
		without = cint(getattr(self, "_plants_without_block", 0))
		if without and self.raised_from != "Bought In":
			notes.append(_(
				"{0} of the {1} plants this plan raises cuttings for have no block free "
				"for their whole life yet. They are included because the TC order has "
				"to be placed long before the blocks are settled -- so placing it "
				"commits to finding room for them."
			).format(without, cint(self.total_plants_to_stick)))
		late = [r for r in self.weeks
		        if getdate(r.week_start_date) < today and cint(r.plants_to_stick)]
		if late:
			notes.append(_(
				"{0} of {1} sticking weeks are already in the past, the earliest "
				"being {2}. Those cuttings cannot now be taken, so the plantings "
				"they feed will be late."
			).format(len(late), len(self.weeks), late[0].week_start_date))
		if self.tc_order_date and getdate(self.tc_order_date) < today:
			notes.append(_(
				"The TC order would have had to be placed on {0}, which is {1} days "
				"ago. Order now and first sticking moves to about {2}."
			).format(self.tc_order_date, (today - getdate(self.tc_order_date)).days,
			         self._earliest_feasible_sticking()))
		if cint(self.cuttings_lost_to_ramp) and self.raised_from != "Bought In":
			notes.append(_(
				"Motherstock reaches full cutting capacity {1} weeks after its first "
				"cut, not on it -- the new pool gets there on {0}. {2} cuttings "
				"across {3} weeks fall inside that climb and cannot be taken. Either "
				"start the pool {1} weeks earlier, or raise it to {4} mother plants "
				"instead of {5} so even the first build-up week is covered, which leaves "
				"that extra capacity idle once the build-up is done."
			).format(self.full_capacity_date, max(0, cint(self.ramp_weeks) - 1),
			         cint(self.cuttings_lost_to_ramp), cint(self.ramp_short_weeks),
			         cint(self.mother_plants_to_cover_ramp),
			         cint(self.mother_plants_required)))
		if cint(self.cuttings_uncovered) and self.raised_from != "Bought In":
			notes.append(_(
				"{0} cuttings are not covered by any source. That is {1} plants that "
				"cannot be stuck, and about {2} stems over their life that the "
				"production plan is still counting."
			).format(cint(self.cuttings_uncovered), cint(self.plants_short),
			         cint(self.stems_at_risk)))
		self.schedule_warning = "\n".join(notes) or None

	def _earliest_feasible_sticking(self):
		"""First sticking date achievable if the TC were ordered today."""
		if not (self.tc_order_date and self.first_sticking_date):
			return None
		lead = (getdate(self.first_sticking_date) - getdate(self.tc_order_date)).days
		return add_days(getdate(), lead)

	def _committed_choice(self):
		"""The TC choice the production plan has committed to, if it has.

		The propagation plan sizes its own order from a probe batch, which is right
		until somebody has decided to buy something else. Then the decision lives on
		the production plan and this document has to follow it, or confirming a
		choice writes a figure here that the next save quietly recomputes away.
		"""
		if not self.production_plan:
			return None
		row = frappe.db.get_value(
			"Summer Flower Production Plan", self.production_plan,
			["tc_choice_committed", "tc_plants_committed", "tc_order_date_committed",
			 "tc_cycles_committed"], as_dict=True)
		return row if row and cint(row.tc_choice_committed) else None

	def _probe_batch(self):
		b = frappe.new_doc("Summer Flower Motherstock Batch")
		b.variety = self.variety
		b.farm = self.farm
		b.protocol = self.protocol
		b.company = self.company
		b.peak_weekly_cuttings = cint(self._peak_shortfall)
		# _peak_shortfall is already CUTTINGS: build_requirement put the plants
		# through the protocol's cuttings_for_plants, which applies the rooting and
		# field losses and then the reject rate. apply_losses defaults on and would
		# multiply by cuttings_per_plant_required a second time -- 8,400 plants
		# became 10,446 cuttings, then 11,951, and the order came out 14% larger
		# than the pool this plan had just sized. The flag is for a peak given in
		# plants; this one is not.
		b.apply_losses = 0
		b.first_sticking_date = self._first_needed
		# A committed build-up is part of the question the probe is answering: it
		# decides both how many plantlets the pool needs and how long they take, so
		# a probe run on the protocol default would date the order by a build-up
		# nobody is doing.
		c = self._committed_choice()
		if c and cint(c.tc_cycles_committed):
			b.build_up_cycles = cint(c.tc_cycles_committed)
		b.run_method("validate")
		return b

	# -------------------------------------------------------------- actions
	@frappe.whitelist()
	def submit_for_approval(self):
		if not self.weeks:
			frappe.throw(_("There is nothing to propagate on this plan."))
		self.status = "Pending Approval"
		self.rejection_reason = None
		self.save()
		return self.status

	@frappe.whitelist()
	def approve(self):
		if not _has_role():
			frappe.throw(_("Only a Farm Manager can approve a propagation plan."))
		if self.status != "Pending Approval":
			frappe.throw(_("Submit the plan for approval first."))
		self.status = "Approved"
		self.approved_by = frappe.session.user
		self.approved_on = now_datetime()
		self.save()
		# Approving the sourcing is what authorises the buying, so the batch is raised
		# here rather than waiting for a third button. Never fatal: an approved plan
		# whose batch could not be raised is still approved, and the chain says so.
		try:
			batch = self.create_motherstock_batch()
			if batch:
				frappe.msgprint(
					_("Motherstock batch {0} raised.").format(
						frappe.utils.get_link_to_form(
							"Summer Flower Motherstock Batch", batch)),
					indicator="green")
		except Exception as e:
			frappe.msgprint(_("Approved, but no motherstock batch was raised: {0}"
			                  ).format(frappe.utils.strip_html(str(e))[:160]),
			                indicator="orange")
		return self.status

	@frappe.whitelist()
	def reject(self, reason=None):
		if not _has_role():
			frappe.throw(_("Only a Farm Manager can reject a propagation plan."))
		if not (reason or "").strip():
			frappe.throw(_("A rejection reason is required."))
		self.status = "Rejected"
		self.rejection_reason = reason
		self.save()
		return self.status

	@frappe.whitelist()
	def create_motherstock_batch(self):
		"""Raise the real Motherstock Batch for the uncovered requirement."""
		if self.status != "Approved":
			frappe.throw(_("Approve the propagation plan first."))
		peak = self._peak_from_rows()
		first = self._first_from_rows()
		if not peak:
			frappe.throw(_(
				"Existing motherstock already covers every week, so there is nothing "
				"to buy."))
		existing = frappe.db.exists("Summer Flower Motherstock Batch", {
			"production_plan": self.production_plan, "variety": self.variety,
			"farm": self.farm, "docstatus": ["<", 2],
			"first_sticking_date": first})
		if existing:
			return existing
		b = frappe.new_doc("Summer Flower Motherstock Batch")
		b.variety = self.variety
		b.farm = self.farm
		b.protocol = self.protocol
		b.company = self.company
		b.production_plan = self.production_plan
		b.peak_weekly_cuttings = peak
		# Same reason as _probe_batch: peak is already in cuttings, scaled up once
		# by cuttings_for_plants. Leaving apply_losses on its default made the real
		# batch order 14% more than the probe had just quoted on the same document.
		b.apply_losses = 0
		b.first_sticking_date = first
		b.flags.ignore_permissions = True
		b.insert()
		frappe.db.commit()
		self.db_set("motherstock_batches_created", frappe.db.count(
			"Summer Flower Motherstock Batch",
			{"production_plan": self.production_plan, "docstatus": ["<", 2]}))
		for r in self.sources:
			if r.source_type == "New Motherstock (TC)" and not r.motherstock_batch:
				frappe.db.set_value("Summer Flower Propagation Source", r.name,
				                    "motherstock_batch", b.name,
				                    update_modified=False)
				break
		return b.name

	def _peak_from_rows(self):
		"""The peak the new pool was asked to cover, not what it managed to supply.

		from_new_ms on its own is the allocation after the build-up, so sizing a batch
		from it sizes the pool from its own ramped output -- circular, and it
		under-orders. The requirement is what the pool had to cover in that week:
		what it did supply plus what was left uncovered. Without this the batch
		ordered 9,949 mother plants against the 11,192 the same document asked for.
		"""
		return max([cint(r.from_new_ms) + cint(r.shortfall) for r in self.weeks] or [0])

	def _first_from_rows(self):
		dates = [getdate(r.week_start_date) for r in self.weeks
		         if cint(r.from_new_ms) + cint(r.shortfall) > 0]
		return min(dates) if dates else None

	@frappe.whitelist()
	def create_seedling_requests(self):
		"""One Seedling Request per sticking week for the propagation unit."""
		if self.status != "Approved":
			frappe.throw(_("Approve the propagation plan first."))
		if not frappe.db.exists("DocType", "Seedling Request"):
			frappe.throw(_("Seedling Request is not installed on this site."))
		meta = frappe.get_meta("Seedling Request")
		made = []
		for r in self.weeks:
			if not cint(r.plants_to_stick):
				continue
			week = "%s-W%02d" % (r.year, cint(r.week_no))
			if meta.has_field("custom_propagation_plan") and frappe.db.exists(
					"Seedling Request", {"custom_propagation_plan": self.name,
					                     "custom_sticking_week": week}):
				continue
			sr = frappe.new_doc("Seedling Request")
			if meta.has_field("title"):
				sr.title = _("{0} {1} - {2} plants").format(
					self.variety, week, cint(r.plants_to_stick))
			if meta.has_field("requested_by"):
				sr.requested_by = frappe.session.user
			if meta.has_field("custom_propagation_plan"):
				sr.custom_propagation_plan = self.name
				sr.custom_sticking_week = week
				sr.custom_plants_required = cint(r.plants_to_stick)
			sr.flags.ignore_permissions = True
			sr.flags.ignore_mandatory = True
			sr.insert()
			made.append(sr.name)
		frappe.db.commit()
		self.db_set("seedling_requests_created", frappe.db.count(
			"Seedling Request", {"custom_propagation_plan": self.name}))
		# Every name, not the first ten: a caller that has just created these has to
		# be able to act on them -- link them, cancel them, clean them up.
		return {"created": len(made), "names": made}


@frappe.whitelist()
def build_from_plan(production_plan, as_dict=False):
	"""The propagation plan for a variety and season: create it, or bring it up to date.

	Keyed on variety and season, not on the production plan. Propagation is one
	physical operation for a crop in a year -- one pool, one bench, one TC order --
	so a second production plan for the same crop and season feeds the same
	propagation plan rather than starting a rival one.

	Where it already exists this rebuilds it and reports what moved. It used to
	return the existing name untouched, which meant every later change to the
	production plan -- a different protocol version, more plantings, a re-sized TC
	order -- was quietly absent from the document the farm actually propagates from.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)

	plan = frappe.get_cached_doc("Summer Flower Production Plan", production_plan)
	existing = frappe.db.get_value(
		"Summer Flower Propagation Plan",
		{"variety": plan.variety, "season_start_year": cint(plan.season_start_year),
		 "status": ["!=", "Rejected"]}, "name")

	if existing:
		d = frappe.get_doc("Summer Flower Propagation Plan", existing)
		if d.docstatus:
			frappe.throw(_(
				"{0} is already submitted, so it cannot be rebuilt. Cancel and amend it "
				"if the plan really has changed."
			).format(d.name))
		before = d.snapshot()
		was_plan = d.production_plan
		d.production_plan = production_plan
		d.flags.ignore_permissions = True
		d.save()
		changes = d.describe_changes(before)
		if was_plan != production_plan:
			changes.insert(0, _("Built from {0} instead of {1}").format(
				production_plan, was_plan))
		d.db_set("last_change_summary", "\n".join(changes) or None,
		         update_modified=False)
		result = {"name": d.name, "created": False, "changes": changes,
		          "variety": d.variety, "season": d.season}
		return result if as_dict else d.name

	d = frappe.new_doc("Summer Flower Propagation Plan")
	d.production_plan = production_plan
	d.flags.ignore_permissions = True
	# No commit here. This runs inside the Procurement Plan's on_submit, and a
	# commit mid-submit means a step that fails afterwards rolls the submit back
	# while leaving the propagation plan -- and everything else already pending --
	# written. Frappe commits the request when it succeeds; that is the guarantee
	# that should hold.
	d.insert()
	result = {"name": d.name, "created": True, "changes": [],
	          "variety": d.variety, "season": d.season}
	return result if as_dict else d.name
