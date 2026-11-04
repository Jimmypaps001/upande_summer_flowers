# Copyright (c) 2026, James Kiruga and contributors
"""How a Production Plan's cuttings get sourced."""

import json
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
from upande_summer_flowers.summer_flowers.planning import (
	iso_monday,
	season_year,
)


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
		self.apply_uplifts()
		self.roll_up()
		self.apply_actuals()

	# --------------------------------------------------------------- header
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
		"""One propagation plan per variety per season PER UNIT."""
		if not (self.variety and self.season_start_year):
			return
		filters = {"variety": self.variety, "season_start_year": self.season_start_year,
		           "status": ["!=", "Rejected"],
		           "propagation_unit": self.propagation_unit or ""}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		dupe = frappe.db.get_value("Summer Flower Propagation Plan", filters, "name")
		if dupe:
			where = _("at {0}").format(self.propagation_unit) if self.propagation_unit \
				else _("with no unit allocated")
			frappe.throw(_(
				"{0} is already the propagation plan for {1} in season {2} {3}. Rebuild "
				"it from the production plan rather than creating a second one -- one "
				"variety at one unit in one season has one pool of motherstock and one "
				"TC order. A different unit may have its own plan."
			).format(dupe, self.variety, self.season, where), title=_("Plan exists"))

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
		self.season_start_year = season_year(p.season_start_year)
		self.season = p.season
		self._plan = p
		self._version = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
		# A pool cuts its way up to full capacity; it does not arrive there. Every
		self._ramp = self._version.ramp_ratios()

	def count_outputs(self):
		"""Count what exists, rather than remembering what was made."""
		self.motherstock_batches_created = frappe.db.count(
			"Summer Flower Motherstock Batch",
			{"production_plan": self.production_plan, "docstatus": ["<", 2]})
		if frappe.db.exists("DocType", "Seedling Request"):
			self.seedling_requests_created = frappe.db.count(
				"Seedling Request", {"custom_propagation_plan": self.name})

	def note_protocol_provenance(self):
		"""Every header field here is the production plan's answer, not a choice."""
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
		"""Plants to stick per week, scaled up into cuttings."""
		v = self._version
		# One definition of "cuttings for these plants", the protocol's own, which
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
			if cint(b.get("not_placed")):
				slot["no_block"] += cint(b.plants)
			if b.planting_year and b.planting_week:
				slot["out"].add("%s-W%02d" % (b.planting_year, cint(b.planting_week)))
				# The day the field wants them. A sticking week says when the
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
		"""Net off what standing motherstock can already cut in each week."""
		v = self._version
		ramp = self._ramp
		per_week = flt(v.cuttings_per_plant_per_week) or 1.0
		# Only a batch the farm has actually committed to. A draft is a working
		batches = frappe.get_all(
			"Summer Flower Motherstock Batch",
			filters={"variety": self.variety, "farm": self.farm,
			         "docstatus": 1,
			         "batch_status": ["!=", "Expired"]},
			fields=["name", "mother_plants", "first_sticking_date", "expiry_date",
			        "batch_status"])
		# What was left out, and why, so a cover figure that just fell does not look
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
		"""Take the pool from the Motherstock Plan instead of sizing one here."""
		self._peak_shortfall = 0
		self._first_needed = None
		self._ms_line = None
		self._at_full = getattr(self, "_at_full", {})
		self.ramp_weeks = len(self._ramp)

		ms = self._ms_line = self.motherstock_line()
		if not ms:
			# Nothing standing to cut from, which for a bought crop is the normal
			self.mother_plants_required = 0
			self.peak_bench_sqm = 0
			self.full_capacity_date = None
			self.cuttings_lost_to_ramp = 0
			self.ramp_short_weeks = 0
			self.mother_plants_to_cover_ramp = 0
			self.new_pool_expiry = None
			self.new_pool_cutting_weeks = 0
			# Said here, in the branch that knows, rather than inferred later from
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
				continue
			# What the field can have is the week's cut less whatever went back as
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
		for g in ms.generation_table:
			self.append("sources", {
				# The vocabulary stays as it was -- these are all the new pool. Which
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
		"""Source a crop that has no motherstock from what is bought for it."""
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
		# What each way in has to deliver, and therefore what has to be bought.
		tc_plants = sum(cint(w.get("by_tc")) for w in (m.get("weeks") or []))
		root_plants = sum(cint(w.get("by_roots")) for w in (m.get("weeks") or []))
		if cint(sg.get("tc")):
			first = (m.get("tc_order_schedule") or [{}])[0].get("week")
			self.append("sources", {
				"source_type": "New Motherstock (TC)",
				"mother_plants": 0,
				"total_cuttings": self._units_for("TC", tc_plants) or cint(sg["tc"]),
				"available_from": first,
				"notes": _("Tissue culture, {0} weeks of lead and {1} in "
				           "propagation before it is plantable. {2} in a hundred "
				           "do not take, so the order is the plants it has to "
				           "stand plus that.").format(
					cint(m.get("tc_lead_weeks")), cint(m.get("tc_weeks_to_ground")),
					round(100 * (1 - self._plants_per_unit("TC")))),
			})
		if cint(sg.get("roots")):
			# When the unit can be worked with, not when it was ordered. Roots
			ro = (m.get("root_order_schedule") or [{}])[0]
			first = ro.get("arrives") or ro.get("week")
			self.append("sources", {
				"source_type": "Bought-in Rooted Cuttings",
				"mother_plants": 0,
				"total_cuttings": self._units_for("Roots", root_plants) or cint(sg["roots"]),
				"available_from": first,
				"notes": _("Roots, {0} weeks of lead and {1} in propagation. One "
				           "root is lifted {2} times and stands {3} plants once "
				           "the losses are off it. They come in once and are held "
				           "in the coldroom, then drawn out week by week as the "
				           "plantings need them.").format(
					cint(m.get("root_lead_weeks")),
					cint(m.get("root_weeks_to_ground")),
					flt(m.get("returns_per_plant")),
					round(self._plants_per_unit("Roots"), 2)),
			})
		self.tc_plants_required = cint(sg.get("tc"))
		if m.get("tc_order_schedule"):
			self.tc_order_date = m["tc_order_schedule"][0]["week"]
		# The agreed order is the order. A suggestion is what to buy if nobody
		c = self._committed_choice()
		if c and cint(c.get("tc_plants_committed")):
			self.tc_plants_required = cint(c["tc_plants_committed"])
			if c.get("tc_order_date_committed"):
				self.tc_order_date = c["tc_order_date_committed"]
		self._cover_from_bought(m)

	def _plants_per_unit(self, stage):
		"""What one bought unit of `stage` actually puts in the ground."""
		from upande_summer_flowers.summer_flowers import sourcing

		if not self.protocol:
			return 1.0
		version = frappe.get_cached_doc("Crop Protocol Version", self.protocol)
		j = sourcing.journey(version, stage)
		return flt((j or {}).get("plants_per_unit")) or 1.0

	def _units_for(self, stage, plants):
		per_unit = self._plants_per_unit(stage)
		return int(math.ceil(cint(plants) / per_unit)) if (plants and per_unit) else 0

	def _root_withdrawals(self, m):
		"""What comes out of the cold store, and when."""
		pulls = m.get("root_withdrawals") or []
		if not pulls:
			return []

		def by_demand():
			out = []
			for pull in pulls:
				w = next((x for x in (m.get("weeks") or [])
				          if str(x.get("root_out_week")) == str(pull["week"])), None)
				out.append({
					"week_start_date": pull["week"], "week_iso": pull["week_iso"],
					"form": "Roots", "source": _("Cold store"),
					"reference": _("One consignment, drawn down"),
					"units": self._units_for("Roots",
					                         cint((w or {}).get("by_roots"))),
					"plants": cint((w or {}).get("by_roots")),
					"ready_on": (w or {}).get("week"),
					"ready_week": (w or {}).get("week_iso"),
					"deliver_on": (w or {}).get("week"),
					"plant_week": (w or {}).get("week_iso"),
				})
			return out

		if not self.propagation_unit:
			return by_demand()
		try:
			from upande_propagation import troughs as T
		except ImportError:
			return by_demand()

		per_root = flt(m.get("returns_per_plant")) or 1.0
		# The trough schedule works in PLANTS -- that is what a trough holds.
		weeks = [(getdate(p["week"]), int(round(cint(p["roots"]) * per_root)))
		         for p in pulls if cint(p["roots"]) > 0]
		if not weeks:
			return by_demand()
		try:
			sched = T.schedule(self.propagation_unit, self.protocol, "Roots", weeks)
		except Exception:
			frappe.clear_last_message()
			return by_demand()
		if sched.get("reason") or not sched.get("rows"):
			return by_demand()

		out = []
		for row in sched["rows"]:
			lifts = row.get("lifts") or []
			first = lifts[0] if lifts else None
			last = lifts[-1] if lifts else None
			out.append({
				"week_start_date": row["planted_on"],
				"week_iso": _iso(row["planted_on"]),
				"form": "Roots", "source": _("Cold store"),
				# Which trough, and what it holds: the two facts that make the
				"reference": _("{0} filled - {1} plants, lifted {2}x").format(
					row["trough"], "{:,}".format(cint(row["plants"])), len(lifts)),
				# Roots out of store, which is the trough\'s fill converted back.
				"units": self._units_for("Roots", cint(row["plants"])),
				"plants": cint(row["plants"]),
				"ready_on": (first or {}).get("ready_on"),
				# _iso(None) is today's week, not "unknown" -- a blank date must
				"ready_week": _iso(first["ready_on"]) if first else "",
				"deliver_on": (last or {}).get("ready_on"),
				"plant_week": _iso(last["ready_on"]) if last else "",
			})
		# What the troughs could not take still has to come out of store, or the
		for when, short in (sched.get("unplaced") or []):
			if cint(short) <= 0:
				continue
			out.append({
				"week_start_date": str(getdate(when)),
				"week_iso": _iso(when),
				"form": "Roots", "source": _("Cold store"),
				"reference": _("No trough free - raised outside or bought as plants"),
				"units": self._units_for("Roots", cint(short)),
				"plants": cint(short),
				"ready_on": None, "ready_week": "", "deliver_on": None, "plant_week": "",
			})
		out.sort(key=lambda r: str(r["week_start_date"]))
		return out

	def _cover_from_bought(self, m):
		"""Cover each week from the material arriving for it, and list it."""
		from upande_summer_flowers.summer_flowers import tc_supply as ts

		by_plant_week = {}
		for w in (m.get("weeks") or []):
			by_plant_week[str(w.get("week"))] = w

		version = frappe.get_cached_doc("Crop Protocol Version", self.protocol)
		for r in self.weeks:
			w = by_plant_week.get(str(r.deliver_on))
			if not w:
				continue
			# `from_bought` is PLANTS -- that is what a lab ships and what the
			r.from_bought = cint(w.get("plants"))
			covered = version.cuttings_for_plants(cint(r.from_bought))
			r.shortfall = max(0, cint(r.cuttings_required)
			                  - cint(r.from_existing_ms) - cint(r.from_new_ms)
			                  - covered)

		self.set("intake", [])
		# Roots first: they are in store a year before any of it, and the unit
		for pull in self._root_withdrawals(m):
			self.append("intake", pull)

		# Then the tissue culture, consignment by consignment and lift by lift,
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
					"units": self._units_for("TC", cint(h["plants"])),
					"plants": cint(h["plants"]),
					"ready_on": h["week"], "ready_week": h["week_iso"],
					# The batcher has already worked out which planting week this
					"deliver_on": h.get("to_farm"),
					"plant_week": h.get("to_farm_iso"),
				})
		elif cint(self.tc_plants_required):
			# Nothing allocated to a lab yet, so the best that can be said is the
			for w in (m.get("weeks") or []):
				if not cint(w.get("by_tc")):
					continue
				self.append("intake", {
					"week_start_date": _back(w.get("week"),
					                         m.get("tc_weeks_to_ground")),
					"week_iso": "", "form": "Tissue Culture",
					"source": _("Not allocated to a lab yet"),
					"units": self._units_for("TC", cint(w["by_tc"])),
					"plants": cint(w["by_tc"]),
					"ready_on": w.get("week"), "ready_week": w.get("week_iso"),
					"deliver_on": w.get("week"), "plant_week": w.get("week_iso"),
				})
		for row in self.intake:
			if row.week_start_date and not row.week_iso:
				row.week_iso = _iso(row.week_start_date)

		# The season total is the consignments added up, not the season rounded
		of_form = {"New Motherstock (TC)": "Tissue Culture",
		           "Bought-in Rooted Cuttings": "Roots"}
		for src in (self.sources or []):
			form = of_form.get(src.source_type)
			if not form:
				continue
			booked = sum(cint(r.units) for r in self.intake if r.form == form)
			if booked:
				src.total_cuttings = booked

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
		per_plant = flt(self.cuttings_per_plant) or 1.0
		self.plants_short = int(self.cuttings_uncovered / per_plant) if per_plant else 0
		self.stems_at_risk = int(round(
			self.plants_short * flt(self._version.total_stems_per_plant_life)))
		self.existing_cover_pct = (
			self.cuttings_from_existing * 100.0 / self.total_cuttings_required
			if self.total_cuttings_required else 0)
		if self.raised_from == "Bought In":
			# A pool that does not exist has no mother plants, no build-up and no
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
			c = self._committed_choice()
			if c:
				if cint(c.tc_plants_committed):
					self.tc_plants_required = cint(c.tc_plants_committed)
				if c.tc_order_date_committed:
					self.tc_order_date = c.tc_order_date_committed
		else:
			# No motherstock line is not no order. A bought crop still has tissue
			if not cint(self.tc_plants_required):
				self.tc_order_date = None
			self.tc_cost = flt(lookup_tc_rate(cint(self.tc_plants_required))) * \
				cint(self.tc_plants_required)
			self.total_cost = flt(self.tc_cost)
			self.tc_on_farm_date = None
			self.first_sticking_date = None
		self.check_schedule()

	def check_schedule(self):
		"""Say plainly when the plan asks for cuttings that can no longer be grown."""
		today = getdate()
		notes = []
		# Sized on every proposed planting, including those with no block. Buying the
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
		"""The TC choice the production plan has committed to, if it has."""
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
		b.apply_losses = 0
		b.first_sticking_date = self._first_needed
		# A committed build-up is part of the question the probe is answering: it
		c = self._committed_choice()
		if c and cint(c.tc_cycles_committed):
			b.build_up_cycles = cint(c.tc_cycles_committed)
		b.run_method("validate")
		return b

	# -------------------------------------------------------------- actions
	@frappe.whitelist()
	# -------------------------------------------------------------- actuals
	def apply_uplifts(self):
		"""Take in cover an accepted re-plan has already added to a forward week."""
		from upande_summer_flowers.summer_flowers import propagation_actuals as pa

		try:
			extra = pa.uplifts(self)
		except Exception:
			frappe.clear_last_message()
			return
		version = frappe.get_cached_doc("Crop Protocol Version", self.protocol)
		for r in self.weeks:
			got = extra.get(getdate(r.deliver_on)) if r.deliver_on else None
			r.uplift_plants = cint(got["plants"]) if got else 0
			if not r.uplift_plants:
				continue
			# Plants in, cuttings out -- same conversion as _cover_from_bought,
			r.from_bought = cint(r.from_bought) + r.uplift_plants
			covered = version.cuttings_for_plants(cint(r.from_bought))
			r.shortfall = max(0, cint(r.cuttings_required)
			                  - cint(r.from_existing_ms) - cint(r.from_new_ms)
			                  - covered)

	def apply_actuals(self):
		"""Set the plan against what the unit actually did, and offer the gap forward."""
		from upande_summer_flowers.summer_flowers import propagation_actuals as pa

		try:
			pa.apply_to(self)
		except Exception:
			frappe.clear_last_message()
			self.actuals_note = _(
				"The actuals could not be read for this rebuild, so every "
				"figure below is the plan rather than what happened:\n{0}"
			).format(frappe.get_traceback(with_context=False)[-400:])

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
		# What the unit is held to, frozen here and never touched again. Without
		self.freeze_baseline()
		self.save()
		# Approving the sourcing is what authorises the buying, so the batch is raised
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

	def freeze_baseline(self):
		"""The approved figures, kept as they were approved."""
		from upande_summer_flowers.summer_flowers import propagation_actuals as pa

		if self.baseline_json:
			return
		self.baseline_json = json.dumps(pa.snapshot(self))
		self.baseline_taken_on = now_datetime()

	@frappe.whitelist()
	def overview_for_form(self):
		"""The three things this plan depends on, which were in three other tabs."""
		return {
			"procurement": self.procurement_summary(),
			"allocations": self.allocation_summary(),
			"planting": self.planting_summary(),
		}

	def _procurement_plans(self):
		"""Every procurement plan for this production plan, newest first."""
		if not self.production_plan:
			return []
		return frappe.get_all(
			"Summer Flower Procurement Plan",
			filters={"production_plan": self.production_plan, "docstatus": ["<", 2]},
			fields=["name", "status", "docstatus", "method", "entry_stage",
			        "supplier", "total_units_to_order", "total_plants_at_field",
			        "plants_per_unit", "first_order_by", "last_order_by",
			        "orders_late", "propagates_here", "beds_required",
			        "beds_available", "capped_by_space"],
			order_by="creation desc")

	def procurement_summary(self):
		return self._procurement_plans()

	def allocation_summary(self):
		"""Who else is raising this season, and how much of it."""
		plans = self._procurement_plans()
		if not plans:
			return {"rows": [], "season_plants": 0, "allocated": 0, "to_buy": 0}

		rows = []
		for p in plans:
			for r in frappe.get_all(
					"Summer Flower Propagation Allocation",
					filters={"parent": p["name"], "parenttype":
					         "Summer Flower Procurement Plan"},
					fields=["propagation_unit", "supplier", "plants", "share_pct",
					        "planted", "surplus", "troughs_used", "plantings",
					        "propagation_plan", "note"],
					order_by="idx"):
				r["procurement_plan"] = p["name"]
				r["entry_stage"] = p["entry_stage"]
				# Which row is this plan's own. Matching on the back-link first
				r["is_this_plan"] = 1 if (
					r.get("propagation_plan") == self.name
					or (not r.get("propagation_plan")
					    and r.get("propagation_unit") == self.propagation_unit)
				) else 0
				rows.append(r)

		season = self.season_plants()
		allocated = sum(cint(r.get("plants")) for r in rows)
		return {
			"rows": rows,
			"season_plants": season,
			"allocated": allocated,
			"to_buy": max(season - allocated, 0),
			"units": len({r["propagation_unit"] for r in rows if r.get("propagation_unit")}),
		}

	def season_plants(self):
		"""The plantings the production plan asks for, which shares are shares of."""
		if not self.production_plan:
			return 0
		try:
			from upande_propagation.allocation import plan_weeks
		except ImportError:
			return cint(frappe.db.get_value("Summer Flower Production Plan",
			                                self.production_plan, "total_plants"))
		try:
			return int(sum(q for _d, q in plan_weeks(self.production_plan)))
		except Exception:
			return 0

	def planting_summary(self):
		"""When what this unit raises actually goes in the ground."""
		if not self.production_plan:
			return {"rows": [], "totals": {}}
		try:
			from upande_summer_flowers.summer_flowers.planning_api import planting_plan
			pp = planting_plan(plan=self.production_plan)
		except Exception as e:
			return {"rows": [], "totals": {}, "error": str(e)}
		rows = pp.get("plantings") or []
		return {
			"rows": rows,
			"totals": pp.get("totals") or {},
			"protocol_status": pp.get("protocol_status"),
			"protocol_stale": cint(pp.get("protocol_stale") or 0),
		}

	@frappe.whitelist()
	def accept_replan(self):
		"""Take the proposal in: the forward weeks carry the gap from here on."""
		from upande_summer_flowers.summer_flowers import propagation_actuals as pa

		if not _has_role():
			frappe.throw(_("Only a Farm Manager can accept a re-plan."))
		return pa.accept(self.name)

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
		"""The peak the new pool was asked to cover, not what it managed to supply."""
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
		return {"created": len(made), "names": made}


@frappe.whitelist()
def build_from_plan(production_plan, as_dict=False, propagation_unit=None):
	"""The propagation plan for a variety, season and unit: create or rebuild it."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)

	plan = frappe.get_cached_doc("Summer Flower Production Plan", production_plan)
	existing = frappe.db.get_value(
		"Summer Flower Propagation Plan",
		{"variety": plan.variety, "season_start_year": season_year(plan.season_start_year),
		 "status": ["!=", "Rejected"],
		 "propagation_unit": propagation_unit or ""}, "name")

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
		if propagation_unit:
			d.propagation_unit = propagation_unit
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
	d.propagation_unit = propagation_unit
	d.flags.ignore_permissions = True
	# No commit here. This runs inside the Procurement Plan's on_submit, and a
	d.insert()
	result = {"name": d.name, "created": True, "changes": [],
	          "variety": d.variety, "season": d.season}
	return result if as_dict else d.name
