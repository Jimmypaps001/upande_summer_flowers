# Copyright (c) 2026, James Kiruga and contributors

import datetime
import math
from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import (cint, flt, get_datetime, getdate, now_datetime,
                          nowdate)

from upande_summer_flowers.summer_flowers.doctype.crop_protocol_version.crop_protocol_version import (
	current_version,
)
from upande_summer_flowers.summer_flowers.doctype.planting_calendar.planting_calendar import (
	RESERVING_STATES,
	production_by_week,
	refresh_coverage,
	standing_plantings,
)
from upande_summer_flowers.summer_flowers.cohort_solver import plan_cohorts
from upande_summer_flowers.summer_flowers.planning import (
	season_year,
	week_family_label,
	MONTH_NAMES,
	season_first_week,
	iso_monday,
	iso_year_week,
	week_sequence,
	weeks_between,
)

# Stop the greedy filler running away if a protocol is misconfigured.
MAX_NEW_PLANTINGS = 400


class SummerFlowerProductionPlan(Document):
	def before_insert(self):
		"""An amendment is a new plan, not a copy that inherits the old one's papers."""
		if self.amended_from:
			self.budget = None

	def validate(self):
		self.pull_header_from_demand()
		self.set_season()
		self.set_period_end()
		self.roll_up_months()
		self.set_totals()
		self.apply_tc_constraint()
		# The trim lands after the totals, so what will actually be planted is
		new_rows = [b for b in self.plan_blocks if b.is_new_planting]
		# What goes IN the ground, kept in step with what stands in it on every
		try:
			v = getattr(self, "_version", None) or frappe.get_cached_doc(
				"Crop Protocol Version", self.protocol)
			# Except on a revision written from confirmed deliveries, where what
			# goes in the ground is what a seller agreed to send, not arithmetic.
			if not self.flags.get("plantings_are_confirmed"):
				for b in new_rows:
					b.plants_to_plant = plants_to_plant(v, cint(b.plants))
		except Exception:
			pass
		# Only where there IS a programme. Where there is not, set_totals has
		# already put the demand's own requirement here, and zeroing it would
		# take the one figure the farm needs in order to buy anything.
		if new_rows or not cint(self.get("sized_from_demand")):
			self.new_beds_required = sum((b.beds or 0) for b in new_rows)
			self.new_plants_required = sum((b.plants or 0) for b in new_rows)
		self.check_protocol_freshness()
		self.sync_status()

	def before_submit(self):
		"""Materials are agreed before approval, not after."""
		if not any(cint(b.is_new_planting) for b in self.plan_blocks):
			return
		# Who supplies it and on what cycles is part of the agreement, not a
		# detail settled afterwards: a house that hands an order over in four
		# lifts plants a different season from one that drops it in a week.
		if not cint(self.supply_confirmed):
			frappe.throw(
				_("Agree the supply for {0} before sending it for approval. "
				  "Open Materials Planning, choose the suppliers and their "
				  "cycles, and confirm them -- what they will hand over, and "
				  "when, is what this plan will be grown from."
				  ).format(self.name),
				title=_("The supply has not been agreed"))
		if cint(self.tc_choice_committed):
			return
		frappe.throw(
			_("Agree the materials for {0} before sending it for approval. It "
			  "proposes {1} plants and nothing has been ordered for them yet: "
			  "open Materials Planning, agree the line, and the plantings will "
			  "settle to what that order can plant. Approving first fixes the "
			  "numbers and the materials card will refuse to change them."
			  ).format(self.name, "{:,}".format(cint(self.new_plants_required))),
			title=_("The materials have not been agreed"))

	def on_submit(self):
		"""Approval is the workflow transition that submits the plan."""
		self.db_set("status", "Approved")
		self.warn_on_space()
		self.create_budget()
		self.raise_what_follows()

	def raise_what_follows(self):
		"""The documents an approved plan commits the farm to."""
		from upande_summer_flowers.summer_flowers.operations_api import (
			documents_that_follow,
		)

		if not cint(self.tc_choice_committed):
			return
		# A revision approved in place of a plan whose material is already
		# ordered raises nothing: the order it answers is the one that caused it.
		if self.flags.get("skip_raise"):
			return
		done, failed = [], []
		for label, fn in documents_that_follow(self.name):
			try:
				out = fn()
				# A step that had nothing to do is not a step that was done.
				if isinstance(out, dict) and out.get("skipped"):
					continue
				done.append(label)
			except Exception as e:
				frappe.log_error(frappe.get_traceback(),
				                 "After approving %s: %s" % (self.name, label))
				failed.append("%s (%s)" % (label, frappe.utils.strip_html(str(e))[:80]))
		if done:
			frappe.msgprint(_("Raised from this plan: {0}.").format(", ".join(done)),
			                indicator="green", alert=True)
		if failed:
			frappe.msgprint(_("Could not raise: {0}. The plan is approved; "
			                  "raise them from their own forms.").format(
				", ".join(failed)), indicator="orange")

	def warn_on_space(self):
		"""Say it now if this plan does not fit the farm."""
		from upande_summer_flowers.summer_flowers import plan_space

		try:
			notes = plan_space.analyse(self)["notes"]
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Plan space for %s" % self.name)
			return
		if notes:
			frappe.msgprint(
				"<br>".join(frappe.utils.escape_html(n) for n in notes)
				+ "<br><br>" + _("Summer Flower Plan Space has the block-by-block "
				                 "figures behind this."),
				indicator="orange", title=_("What this plan asks of the land"))

	def ensure_propagation(self):
		"""Rebuild the propagation plan this crop and season already has."""
		from upande_summer_flowers.summer_flowers.doctype \
			.summer_flower_propagation_plan.summer_flower_propagation_plan import (
				build_from_plan,
			)

		try:
			out = build_from_plan(self.name, as_dict=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(),
			                 "Propagation plan for %s" % self.name)
			frappe.msgprint(
				_("The propagation plan could not be rebuilt. Open the Propagation "
				  "step to see why."),
				indicator="orange", title=_("Propagation not rebuilt"))
			return
		frappe.msgprint(
			_("Propagation plan {0} {1}.").format(
				frappe.utils.get_link_to_form("Summer Flower Propagation Plan",
				                              out["name"]),
				_("created") if out["created"] else
				(_("updated: {0}").format("; ".join(out["changes"]))
				 if out["changes"] else _("rebuilt, nothing changed"))),
			indicator="green")

	def on_cancel(self):
		self.db_set("status", "Rejected")

	# ------------------------------------------------------------------ header
	def pull_header_from_demand(self):
		"""What the market wants comes from the demand. What the crop does does not."""
		if not self.market_demand:
			return
		d = frappe.get_cached_doc("Summer Flower Market Demand", self.market_demand)
		self.variety = d.variety
		# The farm is the register's. The market sheet is stated by farm -- a row is
		if not self.farm:
			self.farm = d.farm
		if d.farm and self.farm != d.farm:
			frappe.throw(_(
				"{0} is the demand register for {1}, but this plan is for {2}. A plan "
				"grows what its register asks for, at the farm that asked for it."
			).format(d.name, d.farm, self.farm))
		if not self.farm:
			frappe.throw(_("Demand register {0} has no farm, so there is nothing for "
			               "the plan to be grown on.").format(d.name))
		# The demand register is authoritative. Do not fall back to whatever
		self.company = d.company or self.company
		# The register no longer carries a currency; the plan is in its company's.
		self.currency = (frappe.get_cached_value("Company", self.company, "default_currency")
		                 if self.company else None) or self.currency
		# A stem is valued at its variety Item's valuation, read from stock.
		from upande_summer_flowers.summer_flowers.valuation import stem_price

		self.price_per_stem = stem_price(self.variety or d.variety, self.company)
		self.resolve_protocol()

	def resolve_protocol(self):
		"""Settle which protocol, and which version of it, this plan is built on."""
		if self.crop_protocol:
			belongs = (frappe.db.get_value("Crop Protocol Version", self.protocol,
			                               "crop_protocol") if self.protocol else None)
			if belongs != self.crop_protocol:
				current = frappe.db.get_value("Crop Protocol", self.crop_protocol,
				                              "custom_sf_current_version")
				if current and frappe.db.exists("Crop Protocol Version", current):
					self.protocol = current
				else:
					rows = frappe.get_all(
						"Crop Protocol Version",
						filters={"crop_protocol": self.crop_protocol,
						         "farm": self.farm, "version_status": "Active"},
						pluck="name", order_by="version desc", limit=1)
					if rows:
						self.protocol = rows[0]
		if not self.protocol:
			self.protocol = current_version(self.variety, self.farm)
			if not self.protocol:
				frappe.throw(_(
					"No Active Crop Protocol Version for {0} at {1}. Approve one "
					"before planning against this demand."
				).format(self.variety, self.farm))

		v = frappe.get_cached_doc("Crop Protocol Version", self.protocol)
		# The form shows the protocol, so it is filled from the version wherever
		if v.crop_protocol and not self.crop_protocol:
			owner_farm = frappe.db.get_value("Crop Protocol", v.crop_protocol, "farm")
			if not owner_farm or owner_farm == self.farm:
				self.crop_protocol = v.crop_protocol
		if v.variety != self.variety or v.farm != self.farm:
			frappe.throw(_(
				"{0} is the protocol for {1} at {2}, but this plan is for {3} at "
				"{4}. A plan cannot be built on another crop's protocol."
			).format(v.name, v.variety, v.farm, self.variety, self.farm))
		self._version = v

	def check_protocol_freshness(self):
		"""Store the freshness verdict so drafts and the list view carry it."""
		if not self.protocol_built_on and not self.is_new():
			# Plans that predate this watermark still have to be judged. The last
			self.protocol_built_on = frappe.db.get_value(
				self.doctype, self.name, "modified")

		f = protocol_freshness(self)
		self.protocol_status = f["status"]
		self.protocol_stale = f["stale"]
		self.protocol_note = f["note"]

	@frappe.whitelist()
	def get_protocol_freshness(self):
		"""Live verdict, for a form that cannot rely on the stored one."""
		return protocol_freshness(self)

	def set_season(self):
		"""The plan covers one growing season: 1 July to 30 June."""
		year = season_year(self.season_start_year)
		if not year:
			# Plans made before the season existed keep the grid they were built on
			if self.from_year and self.from_week:
				self.season = _("{0} weeks from {1}-W{2:02d}").format(
					cint(self.weeks_covered), self.from_year, cint(self.from_week))
			return

		start = datetime.date(year, 7, 1)
		end = datetime.date(year + 1, 6, 30)
		self.season = "%s-%s" % (year, str(year + 1)[2:])

		# A season runs W27 to W26, which is how the demand register numbers it and
		self.from_year, self.from_week = year, season_first_week()
		last_year, last_week = year + 1, season_first_week() - 1
		self.weeks_covered = weeks_between(
			self.from_year, self.from_week, last_year, last_week) + 1

	def set_period_end(self):
		if not (self.from_year and self.from_week and self.weeks_covered):
			return
		grid = week_sequence(self.from_year, self.from_week, self.weeks_covered)
		self.to_year, self.to_week = grid[-1][0], grid[-1][1]

	def sync_status(self):
		"""Keep `status` readable when a workflow drives workflow_state."""
		if self.workflow_state and self.workflow_state in (
			"Draft", "Pending Approval", "Approved", "Rejected"
		):
			self.status = self.workflow_state

	# --------------------------------------------------------------- roll-ups
	def roll_up_months(self):
		buckets = {}
		for w in self.plan_weeks:
			d = getdate(w.week_start_date) if w.week_start_date else iso_monday(w.year, w.week_no)
			key = (d.year, d.month)
			b = buckets.setdefault(key, {"production": 0, "demand": 0})
			b["production"] += w.production_stems or 0
			b["demand"] += w.demand_stems or 0

		total = sum(b["production"] for b in buckets.values())
		self.plan_months = []
		for (year, month) in sorted(buckets):
			b = buckets[(year, month)]
			self.append("plan_months", {
				"year": year,
				"month": month,
				"month_name": MONTH_NAMES[month - 1],
				"production_stems": b["production"],
				"demand_stems": b["demand"],
				"variance_stems": b["production"] - b["demand"],
				"pct_of_year": (b["production"] / total * 100) if total else 0,
			})

	def refresh_placement(self):
		"""Recount what has a block, and what that grows, from the rows as they stand."""
		rows = [b for b in self.plan_blocks if b.is_new_planting]
		self.plantings_not_placed = len([b for b in rows if not b.block])
		self.blocks_used = len({b.block for b in self.plan_blocks if b.block})

		try:
			stems = frappe.parse_json(self.matrix_json or "{}").get("rows") or []
		except Exception:
			stems = []
		if len(stems) != len(self.plan_blocks):
			return
		placeable = defaultdict(int)
		unmet = 0
		for row, mine in zip(self.plan_blocks, stems):
			weekly = (mine or {}).get("weeks") or {}
			if row.block or not row.is_new_planting:
				for key, v in weekly.items():
					placeable[key] += cint(v)
			else:
				unmet += sum(cint(v) for v in weekly.values())
		self.unmet_stems = unmet
		for w in self.plan_weeks:
			w.placeable_production_stems = placeable.get(
				"%s-%s" % (cint(w.year), cint(w.week_no)), 0)

	def set_totals(self):
		self.refresh_placement()
		weeks = self.plan_weeks
		self.total_production_stems = sum((w.production_stems or 0) for w in weeks)
		# What the plan grows, and what today's blocks could hold of it. The first is the
		self.placeable_production_stems = sum(
			(w.get("placeable_production_stems") or 0) for w in weeks)
		self.total_demand_stems = sum((w.demand_stems or 0) for w in weeks)
		self.total_variance_stems = self.total_production_stems - self.total_demand_stems
		self.coverage_pct = (
			self.total_production_stems / self.total_demand_stems * 100
			if self.total_demand_stems else 0
		)
		self.placeable_coverage_pct = (
			self.placeable_production_stems / self.total_demand_stems * 100
			if self.total_demand_stems else 0
		)

		deficits = [
			(w.demand_stems or 0) - (w.production_stems or 0)
			for w in weeks
			if (w.production_stems or 0) < (w.demand_stems or 0)
		]
		self.weeks_in_deficit = len(deficits)
		self.worst_weekly_deficit = max(deficits) if deficits else 0

		areas = [w.area_ha or 0 for w in weeks]
		self.average_area_ha = (sum(areas) / len(areas)) if areas else 0
		years = len(weeks) / 52 if weeks else 0
		self.stems_per_ha_year = (
			self.total_production_stems / self.average_area_ha / years
			if self.average_area_ha and years else 0
		)

		# Every proposed planting, whether a block has been assigned yet or not. These
		new_rows = [b for b in self.plan_blocks if b.is_new_planting]
		# What will actually be planted. Before the materials are agreed that is
		self.new_beds_required = sum((b.beds or 0) for b in new_rows)
		self.new_plants_required = sum((b.plants or 0) for b in new_rows)
		# What the market asked for, kept separately, because every figure that
		self.plants_from_demand = sum(
			(cint(b.get("plants_from_demand")) or cint(b.plants)) for b in new_rows)

		# Motherstock is sized by the biggest single sticking week, never the annual
		sticking = defaultdict(int)
		for b in new_rows:
			if b.sticking_year and b.sticking_week:
				# The peak is what the order has to cover, so it is counted off
				sticking[(b.sticking_year, b.sticking_week)] += (
					cint(b.get("plants_from_demand")) or cint(b.plants) or 0)
		if sticking:
			peak_key = max(sticking, key=lambda k: sticking[k])
			self.peak_weekly_sticking = sticking[peak_key]
			self.peak_sticking_week = f"{peak_key[0]}-W{peak_key[1]:02d}"
		else:
			self.peak_weekly_sticking = 0
			self.peak_sticking_week = None

		# The same peak, kept under its own name because the TC order and the
		planned_sticking = defaultdict(int)
		for b in self.plan_blocks:
			if not b.is_new_planting:
				continue
			if b.sticking_year and b.sticking_week:
				planned_sticking[(b.sticking_year, b.sticking_week)] += b.plants or 0
		# Nothing planted yet, so the order is sized off what the DEMAND asks
		# for. The programme is drawn from the consignments, and the
		# consignments cannot be chosen until the farm is told what to buy, so
		# this figure has to exist before a single planting does.
		self.sized_from_demand = 0
		if not planned_sticking:
			try:
				v0 = getattr(self, "_version", None) or frappe.get_cached_doc(
					"Crop Protocol Version", self.protocol)
				back = cint(weeks_to_a_plant(v0))
				need, from_demand = planting_requirement(self)
				for at, standing, _to_plant in need:
					if not cint(standing):
						continue
					y, w, _d = (getdate(at)
					            - datetime.timedelta(weeks=back)).isocalendar()
					planned_sticking[(cint(y), cint(w))] += cint(standing)
				if planned_sticking and from_demand:
					self.sized_from_demand = 1
					self.plants_from_demand = sum(cint(q) for _a, q, _t in need)
					self.new_plants_required = cint(self.plants_from_demand)
			except Exception:
				frappe.log_error(frappe.get_traceback(),
				                 "Sizing from demand on %s" % self.name)
		if planned_sticking:
			key = max(planned_sticking, key=lambda k: planned_sticking[k])
			self.peak_weekly_sticking_planned = planned_sticking[key]
			self.peak_sticking_week_planned = "%s-W%02d" % key
		else:
			self.peak_weekly_sticking_planned = 0
			self.peak_sticking_week_planned = None
		self.set_tc_order(planned_sticking)

	# --------------------------------------------------------------------- space
	def set_tc_order(self, planned_sticking):
		"""What to buy, on the document that decides it."""
		self.tc_plants_to_order = 0
		self.tc_order_by_date = None
		self.tc_status = None

		v = getattr(self, "_version", None)
		peak = cint(self.peak_weekly_sticking_planned)
		if not v or not peak:
			self.tc_status = (
				_("Nothing is planted yet. This plan says what the market wants "
				  "and what is already standing; the planting programme is drawn "
				  "from the consignments, so choose the suppliers and their "
				  "cycles on Materials Planning and save. Every lift of every "
				  "consignment becomes a planting in the week it lands, and the "
				  "weeks, the space and the order all follow from that.")
				if not supply_is_agreed(self)
				else _("No plantings proposed, so nothing to order."))
			return

		# Through the one function that sizes a purchase, so this figure and the one
		from upande_summer_flowers.summer_flowers import sourcing

		cycles = cint(self.tc_cycles_committed) if self.tc_choice_committed \
			else cint(v.max_multiplication_cycles)
		peak_monday = None
		if self.peak_sticking_week_planned:
			py, pw = self.peak_sticking_week_planned.split("-W")
			peak_monday = iso_monday(cint(py), cint(pw))
		req = sourcing.requirement(v, "TC",
		                           cint(self.plants_from_demand)
		                           or cint(self.new_plants_required), peak,
		                           peak_date=peak_monday)
		if not req:
			self.tc_status = _("This crop's route does not start at TC, so there is no "
			                   "TC order to size. What it does buy is on its Procurement "
			                   "Plan.")
			return
		if req.get("blocked"):
			self.tc_status = req["blocked"]
			return
		cuttings = cint(req.get("weekly_draw"))
		mothers = cint(req.get("pool"))
		calculated = cint(req.get("units"))

		# A confirmed choice is the order; the calculation becomes advice about it.
		if self.tc_choice_committed and cint(self.tc_plants_committed) > 0:
			self.tc_plants_to_order = cint(self.tc_plants_committed)
		else:
			self.tc_plants_to_order = calculated

		# Working back from the first sticking week, through the one function that
		from upande_summer_flowers.summer_flowers.doctype.summer_flower_motherstock_batch.summer_flower_motherstock_batch import (
			tc_order_by_date,
		)

		first = min(planned_sticking) if planned_sticking else None
		if first:
			self.tc_order_by_date = tc_order_by_date(
				v, iso_monday(first[0], first[1]), cycles=cycles)
		agreed_note = None
		if self.tc_choice_committed and self.tc_order_date_committed:
			self.tc_order_by_date = getdate(self.tc_order_date_committed)

		# Once the houses are agreed, the date that binds is theirs: the first
		# consignment's order week at the lead time that house works to. The
		# protocol's own figure is what the draft had to go on before anybody
		# had been asked, and it outlived its usefulness the moment they were.
		agreed = sorted(c.order_week for c in (self.get("plan_consignments") or [])
		                if c.get("order_week"))
		if cint(self.supply_confirmed) and agreed:
			_y, _sep, _w = str(agreed[0]).partition("-W")
			first_order = (iso_monday(cint(_y), cint(_w))
			               if cint(_y) and cint(_w) else None)
			if first_order:
				self.tc_order_by_date = first_order
				agreed_note = (_(
					"Dated from the agreed supply: the first consignment is "
					"ordered in {0}, at the lead time its house works to. The "
					"protocol's own figure no longer applies."
				).format(agreed[0]))

		notes = [agreed_note] if agreed_note else []
		if self.tc_order_by_date and self.tc_order_by_date < getdate(nowdate()):
			notes.append(_(
				"The order date has already passed ({0}), so the first sticking week "
				"{1}-W{2:02d} cannot be met from a new TC order. Buy rooted cuttings "
				"for the early weeks or move the demand later."
			).format(self.tc_order_by_date, first[0], first[1]))
		if cint(self.get("sized_from_demand")):
			notes.append(_(
				"Nothing is planted yet, so this is sized from the demand: "
				"{0} plants to put in the ground over the season, peaking at "
				"{1} in {2}. Agree the suppliers and their cycles and the "
				"planting programme is drawn from what they will bring."
			).format("{:,}".format(cint(self.plants_from_demand)),
			         "{:,}".format(cint(self.peak_weekly_sticking_planned)),
			         self.peak_sticking_week_planned))
		if cint(self.plantings_not_placed):
			notes.append(_(
				"{0} of the proposed plantings are waiting on a block. This order is "
				"sized for the whole plan, which is the point -- it is placed long "
				"before the blocks are assigned."
			).format(cint(self.plantings_not_placed)))
		self.tc_status = "\n".join(notes) or _(
			"{0} plantlets, {1} cycles of multiplication to {2} mother plants, "
			"cutting {3} in the peak week."
		).format(self.tc_plants_to_order, cycles, mothers, cuttings)

	# ------------------------------------------------- what the order supplies
	def apply_tc_constraint(self):
		"""Fill in what the confirmed TC order can actually supply, week by week."""
		# Nothing has been agreed, so nothing is supplied. Seeding this from what
		# the plan WANTS to grow made every draft read as fully supplied -- 125%
		# coverage out of a plan with no supplier, no consignment and no order.
		#
		# A crop cut from our own pool has no supplier to agree with: what
		# supplies it is the mother stock, and the walk below is what says how
		# much of it the one TC order can build. Zeroing that would tell a
		# grower their standing Asters will cut nothing.
		_v = getattr(self, "_version", None)
		if _v is None and self.get("protocol"):
			try:
				_v = frappe.get_cached_doc("Crop Protocol Version", self.protocol)
			except Exception:
				_v = None
		if not supply_is_agreed(self) and not (_v and _cut_from_a_pool(_v)):
			for w in self.plan_weeks:
				w.supplied_production_stems = 0
			self.supplied_production_stems = 0
			self.supplied_coverage_pct = 0
			return
		for w in self.plan_weeks:
			w.supplied_production_stems = cint(w.production_stems)
		self.supplied_production_stems = cint(self.total_production_stems)
		self.supplied_coverage_pct = flt(self.coverage_pct)

		if not (self.tc_choice_committed and cint(self.tc_plants_committed) > 0):
			return
		v = getattr(self, "_version", None) or frappe.get_cached_doc(
			"Crop Protocol Version", self.protocol)
		from upande_summer_flowers.summer_flowers.planning_api import _tc_production

		# The pool walk needs a date to start the line from. A crop that is
		built = None
		order_date = self.tc_order_date_committed or self.tc_order_by_date
		if order_date:
			try:
				built = _tc_production(self, v, cint(self.tc_plants_committed),
				                       getdate(order_date))
			except Exception:
				# A plan that cannot be simulated is still a plan; it just cannot
				frappe.log_error(frappe.get_traceback(),
				                 "TC constraint on %s" % self.name)
				built = None

		if built:
			supplied = {(cint(r["year"]), cint(r["week_no"])): cint(r["production"])
			            for r in built.get("weeks") or []}
			for w in self.plan_weeks:
				w.supplied_production_stems = supplied.get(
					(cint(w.year), cint(w.week_no)), 0)
			self.supplied_production_stems = cint(built.get("production_stems"))
			self.supplied_coverage_pct = flt(built.get("coverage_pct"))
		self.apply_agreed_materials_to_plantings(built, v)

	def plants_the_agreed_units_cover(self, v):
		"""How many plants the agreed TC and roots come to, planting by planting."""
		from upande_summer_flowers.summer_flowers import root_line

		try:
			m = root_line.arrival_plan(self, tc_qty=cint(self.tc_plants_committed),
			                           roots_qty=cint(self.roots_committed))
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Units cover on %s" % self.name)
			return {}
		covered = cint(((m or {}).get("asked") or {}).get("plants_covered"))
		if not covered:
			return {}
		rows = sorted(
			[b for b in self.plan_blocks if cint(b.is_new_planting)],
			key=lambda b: (cint(b.sticking_year), cint(b.sticking_week)))
		# The version's own density. plants_per_bed_for answers 0 on a protocol
		per_bed = cint(v.plants_per_bed) or cint(v.min_planting_plants) or 0
		out, left = {}, covered
		for b in rows:
			want = cint(b.get("plants_from_demand")) or cint(b.plants)
			if left <= 0:
				out[b.name] = 0
				continue
			if left >= want:
				out[b.name] = want
				left -= want
				continue
			take = left
			if per_bed:
				take = (take // per_bed) * per_bed
			out[b.name] = cint(take)
			left = 0
		return out

	def apply_agreed_materials_to_plantings(self, built, v):
		"""Make the plantings say what the agreed materials can actually plant."""
		# A plan re-cut to what the sellers confirmed already says what will be
		# planted. Re-deriving it from the agreement would put back the figure
		# the deliveries just contradicted.
		if self.flags.get("plantings_are_confirmed"):
			return
		# Approved plantings are what somebody approved. The check used to sit
		# below the reset, so approving a plan put every planting back to the
		# demand figure and then returned without re-trimming it: a plan cut to
		# 7,840 plants by its agreed order was approved carrying 9,520.
		if self.docstatus != 0:
			return
		# Recorded for every new planting the first time through, before any
		for b in self.plan_blocks:
			if not cint(b.is_new_planting):
				continue
			if not cint(b.get("plants_from_demand")):
				b.plants_from_demand = cint(b.plants)
			# Put the requirement back before anything measures it. The units
			b.plants = cint(b.plants_from_demand)

		# Which engine answers depends on how the crop is RAISED, not on whether
		pool = bool(cint(v.get("raised_from_pool"))) or any(
			cint(r.is_standing) for r in (v.material_route or []))
		got = (built or {}).get("supplied_by_row") or {}
		if not pool and not any(cint(x) for x in got.values()):
			# Bought, not cut: there is no pool to simulate. Eryngium Scorpius
			got = self.plants_the_agreed_units_cover(v)
		if not got:
			return
		# Supplying NOTHING is not a decision to plant nothing. It is what a
		if not any(cint(x) for x in got.values()):
			return
		per_bed = cint(v.plants_per_bed) or cint(v.min_planting_plants) or 0
		for b in self.plan_blocks:
			if not cint(b.is_new_planting):
				continue
			if not b.plants_from_demand:
				b.plants_from_demand = cint(b.plants)
			if b.name not in got:
				continue
			can = cint(got[b.name])
			b.plants = can
			# plants_to_plant follows from it, and is worked out in one place
			b.trimmed_to_materials = 1 if can < cint(b.plants_from_demand) else 0
			if per_bed:
				b.beds = int(math.ceil(can / float(per_bed))) if can else 0
			b.net_area_ha = planting_area_ha(v, can, b.beds)

	# ------------------------------------------------------------------ budget
	def create_budget(self):
		from upande_summer_flowers.summer_flowers.doctype.summer_flower_budget.summer_flower_budget import (
			build_from_plan,
		)

		if self.budget and frappe.db.exists("Summer Flower Budget", self.budget):
			return self.budget
		name = build_from_plan(self.name)
		self.db_set("budget", name)
		frappe.msgprint(
			_("Budget {0} created with monthly distributions.").format(
				frappe.utils.get_link_to_form("Summer Flower Budget", name)
			),
			indicator="green",
			alert=True,
		)
		return name

	@frappe.whitelist()
	def create_propagation_plan(self):
		"""Work out how this plan's cuttings get sourced."""
		from upande_summer_flowers.summer_flowers.doctype \
			.summer_flower_propagation_plan.summer_flower_propagation_plan import (
				build_from_plan,
			)

		# as_dict so the caller can say whether it created or updated, and what moved.
		return build_from_plan(self.name, as_dict=True)

	def _warn_if_agreement_stale(self, before):
		"""Say so when a rebuild has moved the ground under an agreed order."""
		if not cint(self.get("tc_choice_committed")):
			return
		now = _plants_in(self)
		if not now or not before or abs(now - before) <= max(1, before * 0.01):
			return
		frappe.msgprint(
			_("This plan had {0} plants agreed as material and now needs {1}. "
			  "The procurement and propagation plans still carry the old figure "
			  "— open Materials Planning and save it again to bring them across."
			  ).format(frappe.bold("{:,}".format(before)),
			           frappe.bold("{:,}".format(now))),
			title=_("The agreed material no longer matches the plan"),
			indicator="orange")

	def adopt_protocol_edits(self):
		"""Approve edits sitting on the Crop Protocol, so a rebuild can see them.

		A version is a frozen snapshot, so an edit nobody approved is invisible to
		every plan. Regenerate approves it first rather than rebuilding on the old
		snapshot and reporting success."""
		from upande_summer_flowers.summer_flowers.crop_protocol import (
			approve as approve_protocol, diff_against_current)

		proto = self.crop_protocol or (
			frappe.db.get_value("Crop Protocol Version", self.protocol, "crop_protocol")
			if self.protocol else None)
		if not proto or not frappe.db.exists("Crop Protocol", proto):
			return None
		doc = frappe.get_doc("Crop Protocol", proto)
		changed = diff_against_current(doc)
		if not changed:
			return None
		# A reason is what the history records; approving refuses without one.
		if not doc.get("custom_sf_change_reason"):
			doc.db_set("custom_sf_change_reason",
			           _("Approved while regenerating {0}.").format(self.name),
			           update_modified=False)
		try:
			out = approve_protocol(proto)
		except frappe.PermissionError:
			frappe.throw(
				_("{0} has changes that were never approved ({1}). A plan is built on "
				  "a version, and only an approver writes one, so regenerating would "
				  "rebuild on the old figures. Ask an approver to approve the "
				  "protocol, then regenerate."
				  ).format(proto, ", ".join(changed)), frappe.PermissionError)
		frappe.msgprint(
			_("Approved {0} as {1} first: {2} had changed on the protocol."
			  ).format(proto, out["version"], ", ".join(changed)), indicator="blue")
		return out

	@frappe.whitelist()
	def regenerate(self, adopt_current_protocol=1, approve_protocol_edits=1):
		"""Rebuild the weekly grid and planting proposals against the current protocol."""
		if self.docstatus != 0:
			frappe.throw(_("Only a draft plan can be regenerated."))
		was = self.protocol
		if cint(approve_protocol_edits):
			self.adopt_protocol_edits()
		if cint(adopt_current_protocol):
			current = None
			if self.crop_protocol:
				current = frappe.db.get_value(
					"Crop Protocol", self.crop_protocol, "custom_sf_current_version")
			if not current or not frappe.db.exists("Crop Protocol Version", current):
				current = current_version(self.variety, self.farm)
			if current:
				self.protocol = current
		before = _plants_in(self)
		_populate(self)
		self.save()
		self._warn_if_agreement_stale(before)
		if was and was != self.protocol:
			frappe.msgprint(
				_("Rebuilt on {0}, which is the version in force. It was built on {1}."
				  ).format(self.protocol, was), indicator="blue")

		existing = frappe.db.get_value(
			"Summer Flower Propagation Plan",
			{"variety": self.variety,
			 "season_start_year": season_year(self.season_start_year),
			 "status": ["!=", "Rejected"], "docstatus": 0}, "name")
		if existing:
			self.ensure_propagation()
		return self.name


# ---------------------------------------------------------------------------

def _plants_in(plan):
	"""Plants this plan's new plantings call for."""
	return sum(cint(b.plants) for b in (plan.get("plan_blocks") or [])
	           if cint(b.is_new_planting))


def protocol_freshness(plan):
	"""Whether a plan's stored numbers still match the protocol they cite."""
	blank = {"status": None, "stale": 0, "note": None, "in_force": None}
	if not plan.protocol:
		return blank

	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	status = "v%s · %s%s" % (v.version, v.version_status,
	                         " · in force" if v.is_current else "")
	in_force = current_version(plan.variety, plan.farm)
	if not plan.get("plan_weeks"):
		return {"status": status, "stale": 0, "note": None, "in_force": in_force}

	notes = []
	built = get_datetime(plan.protocol_built_on) if plan.protocol_built_on else None
	if built and get_datetime(v.modified) > built:
		notes.append(_("{0} was edited on {1}. This plan was built on {2}.")
		             .format(v.name, frappe.format(v.modified, "Datetime"),
		                     frappe.format(built, "Datetime")))
	if in_force and in_force != plan.protocol:
		notes.append(_("{0} is now the version in force for {1} at {2}.")
		             .format(in_force, plan.variety, plan.farm))

	# Edits sitting on the Crop Protocol that were never approved. A plan is
	unapproved = None
	if v.crop_protocol:
		unapproved = frappe.db.get_value("Crop Protocol", v.crop_protocol,
		                                 "custom_sf_pending_changes")
	if unapproved:
		notes.append(_("{0} has changes that have not been approved: {1}")
		             .format(v.crop_protocol, unapproved))

	return {
		"status": status,
		"stale": 1 if notes else 0,
		"in_force": in_force,
		"note": "\n".join(notes + [
			_("Approve the protocol first -- that is what writes a version -- "
			  "then Regenerate to rebuild the weekly grid and the planting "
			  "proposals on it.")
			if unapproved else
			_("Regenerate to rebuild the weekly grid and the planting proposals "
			  "from the version in force.")
		]) if notes else None,
	}


# ---------------------------------------------------------------------------

def season_for(date):
	"""The season a date falls in, by its starting year. July starts the season."""
	d = getdate(date)
	return d.year if d.month >= 7 else d.year - 1


@frappe.whitelist()
def block_suggestions(plan, only_unassigned=1):
	"""For each planting, the blocks that could hold it -- nothing is assigned."""
	frappe.has_permission("Summer Flower Production Plan", "read", plan, throw=True)
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	protocol = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	life_weeks = cint(protocol.total_weeks_in_ground)
	only_unassigned = cint(only_unassigned)

	# Seeded with what is already reserved, then each row this plan has already
	cal = BlockCalendar(p.farm, p.variety)
	rows = [b for b in p.plan_blocks if b.is_new_planting and b.planting_date]
	for b in rows:
		if b.block:
			blk = cal.get(b.block)
			if blk:
				blk["busy"].append({
					"start": getdate(b.planting_date),
					"end": getdate(b.planting_date) + datetime.timedelta(weeks=life_weeks),
					"beds": cint(b.beds), "holder": p.name, "status": "This plan",
					"in_ground": False})

	out = []
	for b in rows:
		if only_unassigned and b.block:
			continue
		start = getdate(b.planting_date)
		end = start + datetime.timedelta(weeks=life_weeks)
		out.append({
			"row": b.name,
			"idx": b.idx,
			"beds": cint(b.beds),
			"plants": cint(b.plants),
			"sticking_week": "%s-W%02d" % (b.sticking_year, cint(b.sticking_week)),
			"planting_date": str(start),
			"pinch_date": str(b.pinch_date or ""),
			"first_harvest": ("%s-W%02d" % (b.first_harvest_year,
			                                cint(b.first_harvest_week))
			                  if b.first_harvest_year else None),
			"uproot_date": str(end),
			"assigned": b.block,
			"candidates": cal.candidates(cint(b.beds), start, end),
		})
	return {"plan": p.name, "farm": p.farm, "variety": p.variety,
	        "life_weeks": life_weeks,
	        "awaiting": cint(p.plantings_not_placed),
	        "plantings": out}


@frappe.whitelist()
def assign_block(plan, row, block):
	"""Put one planting in one block, or take it out again."""
	frappe.has_permission("Summer Flower Production Plan", "write", plan, throw=True)
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	if p.docstatus != 0:
		frappe.throw(_("{0} is not a draft, so its blocks cannot be changed.").format(plan))
	target = next((b for b in p.plan_blocks if b.name == row), None)
	if not target:
		frappe.throw(_("That planting is not on this plan."))

	if not block:
		target.block = None
		target.not_placed = 1
	else:
		protocol = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
		start = getdate(target.planting_date)
		end = start + datetime.timedelta(weeks=cint(protocol.total_weeks_in_ground))
		cal = BlockCalendar(p.farm, p.variety)
		for b in p.plan_blocks:
			if b.name != row and b.is_new_planting and b.block and b.planting_date:
				blk = cal.get(b.block)
				if blk:
					blk["busy"].append({
						"start": getdate(b.planting_date),
						"end": getdate(b.planting_date) + datetime.timedelta(
							weeks=cint(protocol.total_weeks_in_ground)),
						"beds": cint(b.beds), "holder": p.name,
						"status": "This plan", "in_ground": False})
		blk = cal.get(block)
		if not blk:
			frappe.throw(_("{0} is not a summer flower block at {1}.").format(block, p.farm))
		free = cal.free_beds(blk, start, end)
		if free < cint(target.beds):
			frappe.throw(_(
				"{0} has {1} of its {2} beds free from {3} to {4}, and this planting "
				"needs {5}. Uproot something earlier, move the planting, or choose "
				"another block."
			).format(block, free, blk["beds"], start, end, cint(target.beds)))
		target.block = block
		target.not_placed = 0
		target.notes = None

	# The weekly grid distinguishes the plan from what today's blocks could hold, so
	p.flags.ignore_permissions = True
	p.save()
	p.reload()
	return {"row": row, "block": target.block,
	        "awaiting": cint(p.plantings_not_placed),
	        "placeable_production_stems": cint(p.placeable_production_stems),
	        "placeable_coverage_pct": flt(p.placeable_coverage_pct),
	        "coverage_pct": flt(p.coverage_pct)}


@frappe.whitelist()
def autoassign_blocks(plan):
	"""Take every suggestion the calendar would make, in one go."""
	frappe.has_permission("Summer Flower Production Plan", "write", plan, throw=True)
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	if p.docstatus != 0:
		frappe.throw(_("{0} is not a draft, so its blocks cannot be changed.").format(plan))
	protocol = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	life = cint(protocol.total_weeks_in_ground)
	cal = BlockCalendar(p.farm, p.variety)
	for b in p.plan_blocks:
		if b.is_new_planting and b.block and b.planting_date:
			blk = cal.get(b.block)
			if blk:
				blk["busy"].append({
					"start": getdate(b.planting_date),
					"end": getdate(b.planting_date) + datetime.timedelta(weeks=life),
					"beds": cint(b.beds), "holder": p.name, "status": "This plan",
					"in_ground": False})
	assigned = []
	for b in p.plan_blocks:
		if not b.is_new_planting or b.block or not b.planting_date:
			continue
		start = getdate(b.planting_date)
		got = cal.place(cint(b.beds), start,
		                start + datetime.timedelta(weeks=life))
		if got:
			b.block, b.not_placed, b.notes = got, 0, None
			assigned.append({"row": b.name, "block": got, "beds": cint(b.beds)})
	p.flags.ignore_permissions = True
	p.save()
	p.reload()
	return {"assigned": assigned, "awaiting": cint(p.plantings_not_placed),
	        "placeable_coverage_pct": flt(p.placeable_coverage_pct)}


#: Below this, a "minimum planting area" is a field nobody filled in rather than
MIN_CREDIBLE_BED_SQM = 5.0


def bed_sqm(v):
	"""How big a bed is, or zero when nobody has said."""
	stated = flt(getattr(v, "sqm_net_per_bed", 0))
	if stated > 0:
		return stated
	fallback = flt(getattr(v, "min_planting_area_sqm", 0))
	return fallback if fallback >= MIN_CREDIBLE_BED_SQM else 0.0


def planting_area_ha(v, plants=0, beds=0):
	"""The ground a planting stands on.

	Off the density first -- that is what the protocol actually measures, and
	it answers for every crop. Bed geometry is the fallback, and it used to be
	the only source: a protocol with no stated bed size reported every planting
	as 0.0000 ha, on the plan, the calendar and the area curve alike."""
	per_sqm = flt(getattr(v, "plants_per_sqm_net", 0))
	if plants and per_sqm > 0:
		return flt(plants) / per_sqm / 10000.0
	return flt(beds) * bed_sqm(v) / 10000.0


def plants_per_bed_for(v):
	"""How many plants a bed holds, worked out if it was not stored."""
	stored = cint(getattr(v, "plants_per_bed", 0))
	if stored:
		return stored
	return int(round(bed_sqm(v) * flt(getattr(v, "plants_per_sqm_net", 0))))



def saleable(v):
	"""The share of cut stems that grade out, as a fraction."""
	keep = 1.0
	for field in ("cutting_reject_pct", "reject_pct"):
		pct = flt(getattr(v, field, 0) or 0)
		if 0 < pct < 100:
			keep *= 1.0 - pct / 100.0
	return keep


def establishment(v):
	"""The share of what is planted that takes, as a fraction. 1.0 when the"""
	pct = flt(getattr(v, "field_establishment_pct", 0) or 0)
	return (pct / 100.0) if 0 < pct < 100 else 1.0


def plants_to_plant(v, standing_required):
	"""How many to put in the ground to be left with `standing_required`."""
	take = establishment(v)
	return int(math.ceil(cint(standing_required) / take)) if take else cint(
		standing_required)


def plants_standing_from(v, planted, at_least=0):
	"""What a planting of `planted` leaves standing once it has established."""
	return max(cint(at_least), int(round(cint(planted) * establishment(v))))


def size_planting(v, plants_required, min_beds=1):
	"""Turn a flush-true plant requirement into a plantable size."""
	per_sqm = flt(getattr(v, "plants_per_sqm_net", 0))
	unit = flt(getattr(v, "min_planting_area_sqm", 0))
	bed = flt(getattr(v, "sqm_net_per_bed", 0))
	ppb = plants_per_bed_for(v)
	if not per_sqm or not unit:
		# No density or no stated minimum: the area arithmetic has nothing to stand
		beds = max(cint(min_beds), int(math.ceil(plants_required / ppb))) if ppb \
			else cint(min_beds)
		return beds, (beds * ppb if ppb else 0), (beds * bed)
	units = max(1, int(math.ceil((plants_required / per_sqm) / unit)))
	area = units * unit
	plants = int(round(area * per_sqm))
	beds = max(cint(min_beds), int(math.ceil(area / bed))) if bed else cint(min_beds)
	return beds, plants, area


@frappe.whitelist()
def uproot_to_free(planting, on_date, reason=None):
	"""Bring a standing planting out early so its block can be used."""
	doc = frappe.get_doc("Planting Calendar", planting)
	when = getdate(on_date)
	planned = getdate(doc.planned_uproot_date) if doc.planned_uproot_date else None
	if planned and when > planned:
		frappe.throw(
			_("{0} is already due out on {1}. Uprooting is for bringing that "
			  "forward, not putting it back.").format(
				doc.name, frappe.format(planned, {"fieldtype": "Date"})),
			title=_("That is later, not earlier"))
	start = doc.effective_planting_date()
	if when < start:
		frappe.throw(
			_("{0} goes in on {1}. It cannot come out before it is planted.").format(
				doc.name, frappe.format(start, {"fieldtype": "Date"})),
			title=_("Before it was planted"))

	# What it costs: the flushes that were still to come after this date.
	lost = [r for r in doc.flush_projection
	        if r.harvest_date and getdate(r.harvest_date) > when
	        and not cint(r.is_harvested)]
	stems = sum(cint(r.expected_stems) for r in lost)
	weeks_early = ((planned - when).days // 7) if planned else 0

	doc.db_set("actual_uproot_date", when, update_modified=False)
	doc.db_set("revision_reason",
	           (reason or _("Uprooted early to free {0}").format(doc.block)),
	           update_modified=False)
	doc.db_set("revised_on", now_datetime(), update_modified=False)
	doc.db_set("revised_by", frappe.session.user, update_modified=False)
	if doc.calendar_status in ("Approved", "Planted"):
		doc.db_set("calendar_status", "Uprooted", update_modified=False)
	refresh_coverage(doc.block)
	return {
		"planting": doc.name, "block": doc.block, "variety": doc.variety,
		"uproot_on": str(when), "weeks_early": weeks_early,
		"flushes_lost": len(lost), "stems_lost": stems,
	}


@frappe.whitelist()
def uprooting_plan(farm=None, season_start_year=None):
	"""Everything being taken out early, and what it costs."""
	f = {"actual_uproot_date": ["is", "set"]}
	if farm:
		f["farm"] = farm
	rows = frappe.get_all(
		"Planting Calendar", filters=f,
		fields=["name", "block", "farm", "variety", "beds", "plants",
		        "planting_date", "actual_planting_date", "planned_uproot_date",
		        "actual_uproot_date", "revision_reason", "calendar_status",
		        "expected_stems_life", "actual_stems_harvested"],
		order_by="actual_uproot_date asc")
	out = []
	for r in rows:
		planned = getdate(r.planned_uproot_date) if r.planned_uproot_date else None
		actual = getdate(r.actual_uproot_date)
		if planned and actual >= planned:
			continue
		out.append(dict(
			r, weeks_early=(planned - actual).days // 7 if planned else 0,
			stems_forgone=max(0, cint(r.expected_stems_life)
			                  - cint(r.actual_stems_harvested)),
		))
	return {"farm": farm, "rows": out,
	        "beds_freed": sum(cint(x["beds"]) for x in out),
	        "stems_forgone": sum(cint(x["stems_forgone"]) for x in out)}


@frappe.whitelist()
def allocation_options(plan):
	"""Each unplaced planting, with the blocks that could take it."""
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	life = cint(v.total_weeks_in_ground)
	turn = cint(v.turnaround_weeks)
	cal = BlockCalendar(p.farm, p.variety)

	rows = []
	for b in p.plan_blocks:
		if not b.is_new_planting or not b.planting_date:
			continue
		start = getdate(b.planting_date)
		end = start + datetime.timedelta(weeks=life + turn)
		cands = cal.candidates(cint(b.beds), start, end)
		# The block this row would take if nobody intervenes. Reserved straight
		suggested = b.block or next((c["block"] for c in cands if c["fits"]), None)
		rows.append({
			"row": b.name,
			"planting_week": "%s-W%02d" % (cint(b.planting_year), cint(b.planting_week)),
			"planting_date": str(start),
			"free_from": str(end),
			"beds": cint(b.beds),
			"plants": cint(b.plants),
			"block": b.block,
			"suggested": suggested,
			"candidates": cands,
		})
		if suggested:
			cal.reserve(suggested, start, end, cint(b.beds))
	return {"plan": p.name, "farm": p.farm, "variety": p.variety, "rows": rows}


@frappe.whitelist()
def allocate(plan, assignments):
	"""Write the chosen blocks onto the plan's plantings."""
	if isinstance(assignments, str):
		assignments = frappe.parse_json(assignments)
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	v = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	life = cint(v.total_weeks_in_ground) + cint(v.turnaround_weeks)
	by_name = {b.name: b for b in p.plan_blocks}

	# Check the whole set before writing any of it. A picker that lets ten two-bed
	cal = BlockCalendar(p.farm, p.variety)
	ordered = sorted(
		[(r, b) for r, b in ((r, by_name.get(r)) for r in (assignments or {})) if b],
		key=lambda rb: getdate(rb[1].planting_date or nowdate()))
	refused = []
	for row, b in ordered:
		block = (assignments or {}).get(row)
		if not block:
			continue
		start = getdate(b.planting_date)
		end = start + datetime.timedelta(weeks=life)
		free = cal.free_beds(cal._get(block), start, end) if cal._get(block) else 0
		if free < cint(b.beds):
			refused.append(_("{0} needs {1} beds in {2}, which has {3} free from "
			                 "{4} to {5}").format(
				"%s-W%02d" % (cint(b.planting_year), cint(b.planting_week)),
				cint(b.beds), block, free, start, end))
			continue
		cal.reserve(block, start, end, cint(b.beds))
	if refused:
		frappe.throw(_("These do not fit:<br><br>{0}<br><br>Nothing was allocated. "
		               "Pick different blocks, or plant less.")
		             .format("<br>".join(refused)), title=_("Will not fit"))

	done = 0
	for row, block in (assignments or {}).items():
		b = by_name.get(row)
		if not b:
			continue
		b.db_set("block", block or None, update_modified=False)
		b.db_set("not_placed", 0 if block else 1, update_modified=False)
		done += 1
	p.reload()
	p.db_set("plantings_not_placed",
	         sum(1 for b in p.plan_blocks if b.is_new_planting and not b.block),
	         update_modified=False)
	frappe.db.commit()
	return {"assigned": done,
	        "still_unplaced": cint(p.plantings_not_placed)}


def _ground_at(farm, start, end):
	"""Beds at this farm, and how many of them are free across the season.

	"64 ha at the farm" answers nothing on its own: what decides whether a plan
	can be planted is the ground nobody has already claimed for those weeks."""
	have = frappe.db.sql("""
		select ifnull(sum(custom_total_beds), 0) beds,
		       ifnull(sum(custom_net_area_ha), 0) ha,
		       count(*) blocks
		from tabBlock where farm = %s and custom_is_summer_flower_block = 1
	""", farm, as_dict=True)[0] if farm else {"beds": 0, "ha": 0, "blocks": 0}
	# Held: anything standing or planned that overlaps the season, wherever it
	# came from. A bed carrying a crop into March is not free in January.
	held = frappe.db.sql("""
		select ifnull(sum(p.beds), 0) beds, count(distinct p.block) blocks
		from `tabPlanting Calendar` p join tabBlock b on b.name = p.block
		where b.farm = %s and ifnull(p.calendar_status, '') not in ('Cancelled', 'Uprooted')
		  and p.planting_date <= %s
		  and ifnull(p.actual_uproot_date, ifnull(p.planned_uproot_date, p.planting_date)) >= %s
	""", (farm, end, start), as_dict=True)[0] if farm else {"beds": 0, "blocks": 0}
	beds, ha = cint(have["beds"]), flt(have["ha"])
	taken = min(cint(held["beds"]), beds)
	per_bed_ha = (ha / beds) if beds else 0
	return {
		"beds": beds, "ha": round(ha, 3), "blocks": cint(have["blocks"]),
		"beds_held": taken, "beds_free": max(0, beds - taken),
		"ha_free": round(max(0.0, ha - taken * per_bed_ha), 3),
		"blocks_in_use": cint(held["blocks"]),
	}


def _bench_at(farm, plants_needed):
	"""Our own propagation units, what they hold and what is already on them."""
	if not frappe.db.has_column("Warehouse", "custom_sf_is_propagation_unit"):
		return None
	rows = frappe.get_all(
		"Warehouse", filters={"custom_sf_is_propagation_unit": 1},
		fields=["name", "custom_farm as farm",
		        "custom_sf_capacity_plants as capacity"])
	if not rows:
		return None
	out, cap, free = [], 0, 0
	for w in rows:
		taken = cint(frappe.db.sql("""
			select ifnull(sum(a.plants), 0)
			from `tabSummer Flower Propagation Allocation` a
			join `tabSummer Flower Procurement Plan` p on p.name = a.parent
			where a.propagation_unit = %s and p.docstatus < 2""", w.name)[0][0])
		mine = max(0, cint(w.capacity) - taken)
		cap += cint(w.capacity)
		free += mine
		out.append({"unit": w.name, "here": 1 if (farm and w.farm == farm) else 0,
		            "capacity": cint(w.capacity), "committed": taken, "free": mine})
	out.sort(key=lambda u: (-u["here"], -u["free"]))
	return {"units": out, "capacity": cap, "free": free,
	        "needed": cint(plants_needed),
	        "short": max(0, cint(plants_needed) - free)}


def _route_of(v):
	"""The road the crop takes, as the protocol draws it."""
	stages = []
	for r in (v.get("material_route") or []):
		if (r.get("row_type") or "Stage") != "Stage":
			continue
		stages.append({"stage": r.stage, "weeks": cint(r.weeks),
		               "loss_pct": flt(r.loss_pct),
		               "bought": 1 if cint(r.is_purchase) else 0})
	return {"summary": v.get("route_summary") or " -> ".join(
		x["stage"] for x in stages), "stages": stages}


def _losses_of(v):
	"""Every loss the plan is grossed up for, in one line."""
	root = flt(v.rooting_success_pct) or 100.0
	field = flt(v.field_establishment_pct) or 100.0
	grade = flt(v.cutting_reject_pct)
	return {"rooting_pct": root, "establishment_pct": field, "grading_pct": grade,
	        "survival_pct": round(root * field / 100.0, 1),
	        "per_plant": flt(v.cuttings_per_plant_required)}


def _cut_from_a_pool(v):
	"""Whether this crop is cut from something standing, or bought and planted."""
	return bool(cint(v.get("raised_from_pool"))
	            or any(cint(r.is_standing) for r in (v.get("material_route") or [])))


def _entry_stage_of(v):
	"""What the crop is bought as, off the route, or nothing where it is not."""
	for r in (v.get("material_route") or []):
		if cint(r.get("is_purchase")) and (r.get("row_type") or "Stage") == "Stage":
			return r.stage
	return None


def _lift_profile(v, stage):
	"""How a consignment of that stage is handed over, where lifts are stated."""
	from upande_summer_flowers.summer_flowers import crop_protocol as cp
	from upande_summer_flowers.summer_flowers.route_ways import LIFT_FORM

	form = LIFT_FORM.get(stage) if stage else None
	if not form:
		return []
	try:
		return [{"lift_no": i + 1, "weeks_after": cint(wk),
		         "share_pct": round(sh * 100, 1)}
		        for i, (wk, sh) in enumerate(cp.protocol_lifts(v, form) or [])]
	except Exception:
		frappe.clear_last_message()
		return []


def _weeks_to_a_plant(v):
	"""Arrival to a plant the field can take: the first lift, else the route."""
	entry = _entry_stage_of(v)
	lifts = _lift_profile(v, entry)
	if lifts:
		return cint(lifts[0]["weeks_after"])
	from upande_summer_flowers.summer_flowers import sourcing

	j = sourcing.journey(v, entry) if entry else None
	return cint(j["weeks_to_ground"]) if j else cint(v.sticking_to_planting_weeks)


@frappe.whitelist()
def plan_preview(market_demand, farm=None, season_start_year=None):
	"""What creating this plan would mean, before anything is written."""
	frappe.has_permission("Summer Flower Market Demand", "read", market_demand, throw=True)
	d = frappe.get_cached_doc("Summer Flower Market Demand", market_demand)
	year = season_year(season_start_year) or season_for(nowdate())
	start = datetime.date(year, 7, 1)
	end = datetime.date(year + 1, 6, 30)

	weeks = [r for r in d.demand_weeks
	         if r.week_start_date and start <= getdate(r.week_start_date) <= end]
	stems = sum(cint(r.demand_stems) for r in weeks)
	firm = sum(cint(r.demand_stems) for r in weeks if r.is_firm)

	notes, blocking = [], []
	# The register names the farm, so the caller no longer has to.
	farm = farm or d.farm
	version = current_version(d.variety, farm) if farm else None
	if not farm:
		blocking.append(_("Demand register {0} has no farm. Set the farm on the "
		                  "register -- it is what the plan is grown on.").format(d.name))
	elif d.farm and farm != d.farm:
		blocking.append(_("{0} asks for stems at {1}, not at {2}.")
		                .format(d.name, d.farm, farm))
	elif not frappe.db.exists("Farm", farm):
		blocking.append(_("{0} is not a Farm.").format(farm))
	elif not version:
		blocking.append(_(
			"No Crop Protocol Version is in force for {0} at {1}. Approve the protocol "
			"first -- every figure in the plan comes from it."
		).format(d.variety, farm))

	if not weeks:
		blocking.append(_(
			"The demand register has no weeks inside season {0}-{1} (1 July {0} to 30 "
			"June {1}). Extend its horizon, or plan a season it covers."
		).format(year, year + 1))

	existing = frappe.db.get_value("Summer Flower Production Plan", {
		"market_demand": market_demand, "farm": farm,
		"season_start_year": year, "docstatus": ["<", 2]}, ["name", "status"], as_dict=True)
	if existing:
		notes.append(_(
			"{0} already plans this variety at this farm for that season ({1}). "
			"Creating another gives you two plans for one commitment; regenerate that "
			"one instead unless you want a rival scenario."
		).format(existing.name, existing.status))

	blocks = frappe.db.count("Block", {"farm": farm,
	                                   "custom_is_summer_flower_block": 1}) if farm else 0
	if farm and not blocks:
		unblocked = frappe.db.sql("""
			select count(*) n, count(distinct b.greenhouse) gh
			from tabBed b join tabWarehouse w on w.name = b.greenhouse
			where w.custom_farm = %s and ifnull(b.custom_block, '') = ''
		""", farm, as_dict=True)[0]
		# A warning, not a bar. This was a blocker because a plan built against a
		notes.append(_(
			"{0} has no summer flower blocks, so there is nowhere to plant yet. It "
			"has {1} beds across {2} greenhouses belonging to no block: draw blocks "
			"over them, or this plan's coverage stands on ground that does not exist."
		).format(farm, cint(unblocked.n), cint(unblocked.gh)) if cint(unblocked.n) else _(
			"{0} has no summer flower blocks and no beds. This plan's coverage will "
			"stand on ground that does not exist until some is drawn."
		).format(farm))

	prop = frappe.db.get_value("Summer Flower Propagation Plan", {
		"variety": d.variety, "season_start_year": year,
		"status": ["!=", "Rejected"]}, ["name", "status"], as_dict=True)
	if prop:
		notes.append(_(
			"{0} is the propagation plan for {1} in that season ({2}). Approving this "
			"plan rebuilds it rather than creating a second one."
		).format(prop.name, d.variety, prop.status))

	# The order that has to be placed first, judged against today rather than left
	lead = None
	if version and weeks:
		v = frappe.get_cached_doc("Crop Protocol Version", version)
		first = min(getdate(r.week_start_date) for r in weeks)
		from upande_summer_flowers.summer_flowers.doctype.summer_flower_motherstock_batch.summer_flower_motherstock_batch import (
			lab_lead_weeks,
		)
		# A crop cut from a pool waits for the lab and for the pool to build.
		# One bought and planted waits for its supplier and for the stage that
		# raises it -- to its FIRST lift, where the protocol states lifts.
		if _cut_from_a_pool(v):
			weeks_back = cint(lab_lead_weeks(v)) + cint(v.lead_time_weeks) \
				+ cint(v.first_harvest_offset_weeks) \
				+ cint(v.sticking_to_planting_weeks)
		else:
			weeks_back = (cint(v.lead_time_weeks) + _weeks_to_a_plant(v)
			              + cint(v.first_harvest_offset_weeks))
		order_by = first - datetime.timedelta(weeks=weeks_back)
		lead = {"first_demand_week": str(first), "weeks_back": weeks_back,
		        "order_by": str(order_by), "late": order_by < getdate(nowdate())}
		if lead["late"]:
			# "The early weeks will be short" is true and useless. Which weeks, and
			earliest_cut = getdate(nowdate()) + datetime.timedelta(weeks=weeks_back)
			missed = [r for r in weeks if getdate(r.week_start_date) < earliest_cut]
			missed_stems = sum(cint(r.demand_stems) for r in missed)
			lead["earliest_first_cut"] = str(earliest_cut)
			lead["weeks_unmeetable"] = len(missed)
			lead["stems_unmeetable"] = missed_stems
			notes.append(_(
				"To harvest in the first demanded week ({0}) the TC order would have "
				"had to be placed by {1} -- {2} weeks earlier, and that date has "
				"passed. Ordering today the first cut is {3}, so {4} weeks and {5} of "
				"the {6} stems demanded this season cannot be met at all. The rest "
				"needs rooted cuttings or a later season."
			).format(first, order_by, weeks_back, earliest_cut, len(missed),
			         "{:,}".format(missed_stems), "{:,}".format(stems)))

	# What it would cost to say yes: the plantlets to buy and the ground to find. Both
	tc = space = None
	ground = _ground_at(farm, start, end) if farm else None
	bench = route = losses = None
	if version and weeks:
		v = frappe.get_cached_doc("Crop Protocol Version", version)
		from upande_summer_flowers.summer_flowers.doctype \
			.summer_flower_motherstock_batch.summer_flower_motherstock_batch import (
				lab_lead_weeks,
			)

		peak_row = max(weeks, key=lambda r: cint(r.demand_stems))
		peak_stems = cint(peak_row.demand_stems)
		offsets = v.flush_offsets()
		first_flush = offsets[0][1] if offsets else 0

		# Several of these are not mandatory on the protocol, so they are easy to
		assumed = []
		if not plants_per_bed_for(v):
			assumed.append(_("plants per bed — needs a bed area and a planting "
			                 "density, and without it no bed or hectare figure can "
			                 "be given at all"))
		if not cint(v.min_planting_beds_derived) and not flt(v.min_planting_area_sqm):
			assumed.append(_("minimum planting area"))
		if _cut_from_a_pool(v) and not flt(v.cuttings_per_plant_per_week):
			assumed.append(_("cuttings per mother plant per week"))
		if not cint(v.total_weeks_in_ground):
			assumed.append(_("weeks in the ground"))
		if not flt(v.total_stems_per_plant_life):
			assumed.append(_("stems per plant over its life"))
		if v.get("bed_area_assumed"):
			notes.append(_(
				"{0} has no bed area recorded, so the smallest planting it will make "
				"({1} m²) is being read as one bed: {2} plants to a bed at {3} per m²."
			).format(farm, flt(v.min_planting_area_sqm), cint(v.plants_per_bed),
			         flt(v.plants_per_sqm_net)))
		if assumed:
			notes.append(_(
				"These are not filled in on {0}, and every figure below assumes 1 "
				"where they should stand: {1}. A 1 is not a measurement — check the "
				"numbers against the protocol before committing to them."
			).format(version, "; ".join(assumed)))

		# Worked out from density and bed area when the version never stored it. If it
		ppb = plants_per_bed_for(v)
		min_beds = cint(v.min_planting_beds_derived) or (1 if ppb else 0)
		if first_flush:
			# The busiest week decides the pool, because cuttings cannot be banked.
			plants_required = int(math.ceil(peak_stems / first_flush))
			plants = plants_required
			if ppb:
				# Rounded to a whole minimum planting area, not to a whole bed: see
				beds, plants, _a = size_planting(v, plants_required, min_beds)
			else:
				beds = 0
			from upande_summer_flowers.summer_flowers import sourcing

			# The route's own way in, not "TC". Asking for tissue culture on a
			# crop raised from seed answered None, and the dialog then said
			# nothing at all about the material it has to buy.
			entry = _entry_stage_of(v)
			req = sourcing.requirement(v, entry, plants, plants) if entry else None
			if not entry:
				# Nothing is bought: the crop comes off the farm's own standing
				# stock. Saying nothing at all read as a figure that failed.
				notes.append(_(
					"Nothing is bought for this crop. {0} says it comes off the "
					"farm's own stock, so there is no order and no lead time -- "
					"only ground to find and a bench to cut from."
				).format(version))
			cuttings = cint(req.get("weekly_draw")) if req else 0
			per_week = flt(v.cuttings_per_plant_per_week) or 1.0
			mothers = cint(req.get("pool")) if req else 0
			tc = req and {
				# Which road this crop takes. A pool is cut from; everything else
				# is bought and planted, and the two read nothing alike.
				"pool": 1 if (req.get("kind") == "standing") else 0,
				"stage": entry,
				"units": cint(req.get("units")),
				"plants_per_unit": flt(req.get("plants_per_unit")),
				"weeks_to_plant": cint(req.get("weeks_to_ground")),
				"lifts": _lift_profile(v, entry),
				# Which week, not just how big. "The peak week" means nothing without
				"peak_week": "%s-W%02d" % (cint(peak_row.year), cint(peak_row.week_no)),
				"peak_week_stems": peak_stems,
				"plants_in_peak_week": plants,
				# What the flush asks for, beside what the bed grid delivers. When the
				"plants_the_flush_asks_for": plants_required,
				"cuttings": cuttings,
				"mother_plants": mothers,
				# Carried so the reader can see why mothers and cuttings are often the
				"cuttings_per_plant_per_week": per_week,
				"plantlets": cint(req.get("units")),
				"blocked": req.get("blocked"),
				"kind": req.get("kind"),
				"order_by": lead["order_by"] if lead else None,
				"late": bool(lead and lead["late"]),
			}
			route = _route_of(v)
			route["lifts"] = _lift_profile(v, entry)
			losses = _losses_of(v)
			# Ground: what has to be STANDING to deliver the demand, which is a rate
			weeks_in_ground = cint(v.total_weeks_in_ground)
			per_week = (flt(v.total_stems_per_plant_life) / weeks_in_ground
			            if weeks_in_ground else 0)
			if per_week:
				total_plants = int(math.ceil(peak_stems / per_week))
			if per_week and ppb:
				smooth_beds = int(math.ceil(total_plants / ppb))
				# That figure assumes the ground can be divided as finely as the demand.
				covered = defaultdict(int)
				proposed_beds = 0
				for r in sorted(weeks, key=lambda x: (cint(x.year), cint(x.week_no))):
					key = (cint(r.year), cint(r.week_no))
					short = cint(r.demand_stems) - covered[key]
					if short <= 0:
						continue
					want = int(math.ceil(short / (first_flush * ppb)))
					b = max(min_beds, want)
					proposed_beds += b
					for off, spp in offsets:
						# hd, not d: d is the demand register in this scope, and
						hd = getdate(r.week_start_date) + datetime.timedelta(
							weeks=off - offsets[0][0])
						covered[iso_year_week(hd)] += int(round(spp * b * ppb))
				total_beds = max(smooth_beds, proposed_beds)
				need_ha = planting_area_ha(v, total_plants, total_beds)
				have = frappe.db.sql("""
					select ifnull(sum(custom_net_area_ha), 0) ha,
					       ifnull(sum(custom_total_beds), 0) beds
					from tabBlock where farm = %s and custom_is_summer_flower_block = 1
				""", farm, as_dict=True)[0] if farm else {"ha": 0, "beds": 0}
				bench = _bench_at(farm, total_plants)
				space = {
					"basis": "plants standing to deliver the busiest week (%s stems) "
					         "at %.3f stems per plant per week" % (peak_stems, per_week),
					# Both, so the cost of the minimum is visible rather than folded in.
					"beds_if_divisible": smooth_beds,
					"beds_with_minimum": proposed_beds,
					"min_planting_beds": min_beds,
					"min_planting_area_sqm": flt(v.min_planting_area_sqm),
					"beds_needed": total_beds, "plants_needed": total_plants,
					"ha_needed": round(need_ha, 3),
					"ha_at_farm": round(flt(have["ha"]), 3),
					"beds_at_farm": cint(have["beds"]),
					"pct_of_farm": round(need_ha / flt(have["ha"]) * 100, 1)
					if flt(have["ha"]) else None,
				}
				if space["pct_of_farm"] and space["pct_of_farm"] > 100:
					notes.append(_(
						"This demand needs about {0} ha standing ({1} beds) and {2} has "
						"{3} ha. It will not all fit, so expect plantings with no block."
					).format(space["ha_needed"], space["beds_needed"], farm,
					         space["ha_at_farm"]))
		# Nothing about tissue culture where the crop is not raised from it, and no
		if tc and tc["blocked"]:
			notes.append(tc["blocked"])
		elif tc:
			when = (_(" The order was due {0}.").format(tc["order_by"]) if tc["late"]
			        else (_(" Order by {0}.").format(tc["order_by"])
			              if tc["order_by"] else ""))
			if tc["pool"]:
				notes.append(_(
					"About {0} plantlets to buy. They multiply into {1} mother plants, "
					"which is what it takes to cut {2} cuttings in {3} -- the busiest "
					"sticking week, and the one the pool has to be sized for because "
					"cuttings cannot be banked.{4}"
				).format("{:,}".format(tc["plantlets"]),
				         "{:,}".format(tc["mother_plants"]),
				         "{:,}".format(tc["cuttings"]), tc["peak_week"], when))
			else:
				# The same paragraph used to be written over a crop with no
				# motherstock, which read "they multiply into 0 mother plants,
				# enough to cut 0 cuttings".
				lifts = tc.get("lifts") or []
				how = (_(" It is handed over in {0} lifts: {1}.").format(
					len(lifts), ", ".join("%s%% at %sw" % (L["share_pct"],
					                                       L["weeks_after"])
					                      for L in lifts))
					if len(lifts) > 1 else "")
				notes.append(_(
					"About {0} {1} to buy. There is no motherstock: it is planted as "
					"it is raised, {2} weeks from arrival to a plant the field can "
					"take, and {3} plants have to go in the ground in {4} -- the "
					"busiest planting week.{5}{6}"
				).format("{:,}".format(cint(tc["units"]) or cint(tc["plantlets"])),
				         (tc["stage"] or "TC").lower(),
				         cint(tc["weeks_to_plant"]),
				         "{:,}".format(cint(tc["plants_in_peak_week"])),
				         tc["peak_week"], how, when))

	return {
		"variety": d.variety, "farm": farm, "season": "%s-%s" % (year, str(year + 1)[-2:]),
		"season_start_year": year,
		"tc": tc, "space": space,
		"season_start": str(start), "season_end": str(end),
		"weeks": len(weeks), "demand_stems": stems, "firm_stems": firm,
		"peak_week_stems": max((cint(r.demand_stems) for r in weeks), default=0),
		"protocol": version, "blocks": blocks,
		"existing_plan": existing.name if existing else None,
		"propagation_plan": prop.name if prop else None,
		"lead": lead, "notes": notes, "blocking": blocking,
		"ground": ground, "bench": bench, "route": route, "losses": losses,
		"can_create": not blocking,
	}


@frappe.whitelist()
def build_from_demand(market_demand, farm=None, season_start_year=None,
                      from_year=None, from_week=None, weeks=None):
	"""Create a draft Production Plan for one growing season of a demand register."""
	demand = frappe.get_doc("Summer Flower Market Demand", market_demand)
	if not demand.demand_weeks:
		frappe.throw(_("Demand register {0} has no weeks.").format(market_demand))

	first = demand.demand_weeks[0]
	farm = farm or demand.farm
	if farm and not frappe.db.exists("Farm", farm):
		# Caught a year arriving here as the farm once already, from a positional call
		frappe.throw(_("{0} is not a Farm. Name the farm this plan is grown on.")
		             .format(farm))
	plan = frappe.new_doc("Summer Flower Production Plan")
	plan.market_demand = market_demand
	if farm:
		plan.farm = farm
	# One plan covers one season. Defaults to the season the register opens in, so a
	plan.season_start_year = season_year(season_start_year) or season_for(
		first.week_start_date or iso_monday(cint(first.year), cint(first.week_no)))
	plan.pull_header_from_demand()
	plan.set_season()

	_populate(plan)
	plan.insert()
	return plan.name


def planting_requirement(plan):
	"""[(Monday of the planting week, plants standing, plants to plant), ...].

	Its own programme where it has one; what the demand asks for where it does
	not. A plan with nothing planted yet still has to say what to buy -- that
	is the whole question the materials card is opened to answer, and the
	programme is drawn FROM the answer, not the other way round.

	Both figures, because the two cards mean different things by "plants". A
	buyer buys what goes IN the ground; the field counts what is left standing
	once it has established. Handing one number to both is how the material
	card came to say 445 tc and the supply card 228 for the same planting."""
	rows = {}
	for b in (plan.get("plan_blocks") or []):
		if not cint(b.is_new_planting) or not b.get("planting_date"):
			continue
		at = getdate(b.planting_date)
		at = at - datetime.timedelta(days=at.weekday())
		standing, to_plant = rows.get(at, (0, 0))
		rows[at] = (standing + cint(b.plants),
		            to_plant + (cint(b.get("plants_to_plant")) or cint(b.plants)))
	if rows:
		return [(at, q[0], q[1]) for at, q in sorted(rows.items())], False

	# Sized exactly the way _populate sizes a planting, because it is the same
	# planting: grossed up for establishment, rounded to the smallest the
	# protocol allows, and read back down to what that stands up.
	try:
		wanted = wanted_plants_by_week(plan) or {}
		if not wanted:
			return [], True
		v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
		min_beds = cint(v.min_planting_beds_derived) or 1
		out = []
		for at, plants in sorted(wanted.items()):
			if not cint(plants):
				continue
			_beds, planted, _a = size_planting(
				v, plants_to_plant(v, cint(plants)), min_beds)
			planted = cint(planted) or plants_to_plant(v, cint(plants))
			out.append((at, plants_standing_from(v, planted, cint(plants)),
			            cint(planted)))
		return out, True
	except Exception:
		frappe.log_error(frappe.get_traceback(),
		                 "Demand requirement on %s" % plan.get("name"))
		return [], True


def requirement_rows(plan):
	"""The plantings the demand asks for, before any have been drawn.

	The same shape the plan's own rows are read in, so the planting plan can
	show what has to go in the ground while the supply is still being settled.
	A requirement nobody can see is a requirement nobody can buy for."""
	need, from_demand = planting_requirement(plan)
	if not need or not from_demand:
		return []
	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	offsets = v.flush_offsets() or []
	anchor = cint(sorted(offsets, key=lambda o: o[0])[0][0]) if offsets else 0
	life_weeks = cint(v.total_weeks_in_ground)
	min_beds = cint(v.min_planting_beds_derived) or 1
	back = cint(weeks_to_a_plant(v))
	# Beds can only be counted where the protocol says how big one is. Without
	# it size_planting falls back to the minimum and every planting reads as
	# one bed, whatever its area -- so the row says so rather than letting the
	# figure be read as ground.
	no_bed_size = not flt(v.get("sqm_net_per_bed"))
	out = []
	for i, (at, standing, to_plant) in enumerate(need, start=1):
		at = getdate(at)
		beds, _planted, _a = size_planting(v, to_plant, min_beds)
		stick = at - datetime.timedelta(weeks=back)
		sy, sw, _d = stick.isocalendar()
		py, pw, _d = at.isocalendar()
		fh = at + datetime.timedelta(weeks=anchor)
		fy, fw, _d = fh.isocalendar()
		out.append({
			"idx": i,
			"awaiting_supply": 1,
			"is_new": True,
			"block": None,
			"existing_planting": None,
			"beds": cint(beds),
			"plants": cint(standing),
			"plants_to_plant": cint(to_plant),
			"cuttings": (v.cuttings_for_plants(cint(standing))
			             if cint(standing) else 0),
			"season_stems": 0,
			"stick": "%d-W%02d" % (cint(sy), cint(sw)),
			"plant": "%d-W%02d" % (cint(py), cint(pw)),
			"planting_year": cint(py), "planting_week": cint(pw),
			"sticking_year": cint(sy), "sticking_week": cint(sw),
			"planting_date": str(at),
			"sticking_date": str(stick),
			"pinch_date": None,
			"first_harvest": "%d-W%02d" % (cint(fy), cint(fw)),
			"first_harvest_date": str(fh),
			"first_harvest_year": cint(fy), "first_harvest_week": cint(fw),
			"uproot_date": str(at + datetime.timedelta(weeks=life_weeks)),
			"harvest_family": None,
			"net_area_ha": planting_area_ha(v, cint(standing), cint(beds)),
			"lifetime_stems": int(round(
				flt(v.total_stems_per_plant_life or 0) * cint(standing))),
			"below_minimum": False,
			"in_past": getdate(at) < getdate(nowdate()),
			"unallocated": True,
			"not_placed": True,
			"row": None,
			"notes": _("Not drawn yet. This is what the demand asks for; the "
			           "planting is settled when the supply is agreed.")
			+ (_(" Beds cannot be counted: {0} does not say how many m² a bed "
			     "is, so this reads as one bed whatever its area. The area is "
			     "worked off the density and is right.").format(v.name)
			   if no_bed_size else ""),
		})
	return out


def weeks_to_a_plant(v):
	"""Arrival to a plant the field can take: the first lift, else the route."""
	lifts = _lift_profile(v, _entry_stage_of(v))
	return (cint(lifts[0]["weeks_after"]) if lifts
	        else cint(v.sticking_to_planting_weeks))


def wanted_plants_by_week(plan):
	"""{Monday of the planting week: plants the demand asks to go in the ground}.

	Worked from the demand curve and what is already standing, with no
	reference to the plan's own plantings. That is the whole point: this is
	what the consignments are sized against, and it has to be answerable
	before a single planting exists."""
	if not plan.get("protocol") or not plan.get("market_demand"):
		return {}
	protocol = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	offsets = protocol.flush_offsets()
	if not offsets:
		return {}
	demand = frappe.get_doc("Summer Flower Market Demand", plan.market_demand)
	grid = week_sequence(plan.from_year, plan.from_week, plan.weeks_covered)
	if not grid:
		return {}
	index = {(y, w): i for i, (y, w, _d) in enumerate(grid)}
	demand_map = {(r.year, r.week_no): cint(r.demand_stems)
	              for r in demand.demand_weeks if (r.year, r.week_no) in index}
	production = defaultdict(int)
	for planting in standing_plantings(plan.farm, plan.variety):
		for (y, w), stems in production_by_week(planting).items():
			if (y, w) in index:
				production[(y, w)] += stems

	curve = sorted(offsets, key=lambda o: o[0])
	anchor = cint(curve[0][0])
	keep = saleable(protocol)
	rates = [0.0] * (cint(curve[-1][0]) - anchor + 1)
	for off, spp in curve:
		rates[cint(off) - anchor] = flt(spp) * keep

	tolerance = flt(frappe.db.get_single_value(
		"Summer Flower Settings", "weekly_variance_tolerance_pct"))
	band = (tolerance if tolerance > 0 else 10.0) / 100.0
	min_beds = cint(protocol.min_planting_beds_derived) or 1
	_b, step, _a = size_planting(protocol, 1, min_beds)
	cohorts, _solved = plan_cohorts(
		rates,
		[demand_map.get((y, w), 0) for (y, w, _m) in grid],
		[production[(y, w)] for (y, w, _m) in grid],
		band=band, rounding_plants=max(1, cint(step)))

	window_start = grid[0][2]
	out = {}
	for start, plants in sorted(cohorts.items()):
		at = window_start + datetime.timedelta(weeks=cint(start) - anchor)
		out[at] = cint(out.get(at, 0)) + cint(plants)
	return dict(sorted(out.items()))


def programme_from_supply(plan):
	"""The planting programme the agreed consignments actually make possible.

	A consignment is not a planting. It is handed over in lifts, and each lift
	is its own planting in the week it becomes a plant the field can take. The
	week a consignment is named for is its FIRST lift; the others follow it,
	which is how one order comes to fill several planting weeks."""
	from upande_summer_flowers.summer_flowers import tc_supply as ts
	from upande_summer_flowers.summer_flowers.route_ways import LIFT_FORM

	rows = plan.get("plan_consignments") or []
	if not rows:
		return []
	v = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	form = LIFT_FORM.get(_entry_stage_of(v)) or "Tissue Culture"
	# A lift too small to fill the protocol's smallest planting does not get a
	# bed of its own: it joins the lift before it. Otherwise a 10% tail of a
	# small consignment is rounded up to a whole bed and the plan plants four
	# times what was bought.
	min_beds = cint(v.min_planting_beds_derived) or 1
	floor = cint(size_planting(v, 1, min_beds)[1])
	by_house, out = {}, []
	for c in rows:
		at = getdate(c.lands_date) if c.get("lands_date") else None
		plants = cint(c.get("plants"))
		if not at or plants <= 0:
			continue
		house = (c.get("supplier") or "").strip()
		if house not in by_house:
			by_house[house] = (ts.handover_for(form, house or None, 0, v)
			                   or [(0, 1.0)])
		waves = by_house[house]
		first = cint(waves[0][0])
		running, base = 0, len(out)
		for i, (wk, share) in enumerate(waves):
			# The last lift takes the remainder, so the lifts add to the order.
			got = (plants - running if i == len(waves) - 1
			       else int(round(plants * flt(share))))
			running += got
			if got <= 0:
				continue
			when = at + datetime.timedelta(weeks=cint(wk) - first)
			if len(out) > base and got < floor:
				out[-1] = (out[-1][0], out[-1][1] + got)
				continue
			out.append((when, got))
	return sorted(out)


def rebuild_from_supply(plan):
	"""Re-draw the plan from the supply that was agreed. Does not save."""
	doc = (plan if hasattr(plan, "doctype")
	       else frappe.get_doc("Summer Flower Production Plan", plan))
	# Consignments draw the programme only where the field plants what was
	# bought. Asters are bought as TC once, built up, and planted from
	# cuttings off our own mother stock -- so a TC order for the pool must not
	# re-date the field. What paces those plantings is what the pool can give
	# in a week, and the solver already answers that.
	v = (frappe.get_cached_doc("Crop Protocol Version", doc.protocol)
	     if doc.get("protocol") else None)
	programme = None if (v and _cut_from_a_pool(v)) else programme_from_supply(doc)
	# An empty programme is not the same as no programme: passing one would
	# plant nothing at all and wipe what is already there.
	_populate(doc, programme=programme or None)
	return doc


def supply_is_agreed(plan):
	"""Whether the houses that will supply this plan have been settled."""
	return bool(cint(plan.get("supply_confirmed"))
	            or (plan.get("plan_consignments") or []))


def _populate(plan, programme=None):
	"""Fill plan_weeks and plan_blocks.

	`programme` is the planting programme as [(planting date, plants), ...].
	Where one is given it IS the programme: it comes from the consignments the
	farm agreed with its houses, and a consignment handed over in lifts plants
	in several weeks, not one. Where none is given and no supply has been
	agreed, nothing new is planted at all -- a plan drawn before anybody has
	been asked what they can bring is a wish, and the weeks it filled in read
	exactly like weeks that had been bought."""
	demand = frappe.get_doc("Summer Flower Market Demand", plan.market_demand)
	if not plan.protocol:
		plan.resolve_protocol()
	protocol = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	# Watermark the version as it stands at this moment, so a later edit to it can
	plan.protocol_built_on = protocol.modified

	grid = week_sequence(plan.from_year, plan.from_week, plan.weeks_covered)
	index = {(y, w): i for i, (y, w, _d) in enumerate(grid)}

	demand_map = {
		(r.year, r.week_no): (r.demand_stems or 0)
		for r in demand.demand_weeks
		if (r.year, r.week_no) in index
	}

	production = defaultdict(int)
	# A plan answers what the market wants and what the crop does. Whether a block is
	placeable = defaultdict(int)
	contributors = defaultdict(list)
	# Every planting's own week-by-week stems, keyed by its plan_blocks row. This is
	per_row = []
	# (start_date, end_date, net_area_ha) for the area-standing curve
	footprints = []

	# ---- what is already on the ground
	plan.plan_blocks = []
	for planting in standing_plantings(plan.farm, plan.variety):
		hit = False
		mine = defaultdict(int)
		for (y, w), stems in production_by_week(planting).items():
			if (y, w) in index:
				production[(y, w)] += stems
				placeable[(y, w)] += stems
				mine[(y, w)] += stems
				contributors[(y, w)].append(f"{planting.block} ({planting.beds} beds)")
				hit = True
		footprints.append((
			getdate(planting.planting_date), planting.end_date(),
			planting.net_area_ha or 0,
		))
		if hit:
			fh_year, fh_week = planting.first_harvest()
			# `existing_planting` links a Planting Calendar and only that -- the
			from_cycle = getattr(planting, "doctype", "") == "Crop Cycle"
			plan.append("plan_blocks", {
				"is_new_planting": 0,
				"block": planting.block,
				"existing_planting": None if from_cycle else planting.name,
				"notes": (_("Standing crop, from cycle {0}").format(planting.name)
				          if from_cycle else None),
				"beds": planting.beds,
				"plants": planting.plants,
				"planting_year": planting.planting_year,
				"planting_week": planting.planting_week,
				"planting_date": planting.planting_date,
				"pinch_date": planting.pinch_date,
				"first_harvest_year": fh_year,
				"first_harvest_week": fh_week,
				"harvest_week_family": planting.harvest_week_family,
				"net_area_ha": planting.net_area_ha,
				"lifetime_stems": planting.expected_stems_life,
				"planting_in_past": 1 if getdate(planting.planting_date) < getdate(nowdate()) else 0,
			})
			per_row.append({"weeks": dict(mine), "uproot": str(planting.end_date())})

	# ---- close the remaining deficits
	offsets = protocol.flush_offsets()
	if not offsets:
		cycle = protocol.growing_cycle or _("unset")
		need = {
			protocol.CONTINUOUS: _("weeks planting to first cut, productive weeks "
			                       "and stems per plant per week"),
			protocol.SINGLE: _("weeks planting to first cut and lifetime stems "
			                   "per plant"),
		}.get(cycle, _("a flush schedule"))
		frappe.throw(
			_("Protocol {0} is a {1} crop and needs {2} before production can be "
			  "projected.").format(frappe.bold(protocol.name), cycle, need),
			title=_("Nothing to project"))

	# The curve as a run of weeks from the first productive one, which is the
	curve = sorted(offsets, key=lambda o: o[0])
	anchor = cint(curve[0][0])
	# Stems that can be SOLD, not stems cut. A share of every harvest fails
	keep = saleable(protocol)
	rates = [0.0] * (cint(curve[-1][0]) - anchor + 1)
	for off, spp in curve:
		rates[cint(off) - anchor] = flt(spp) * keep
	life_weeks = protocol.total_weeks_in_ground or 0
	# Arrival to a plant the field can take. Where the protocol states lifts,
	# that is the FIRST lift: a consignment handed over in waves is not a plant
	# on the day it lands, and dating the order off the route's own weeks
	# ordered every lifted crop late. This is the same figure the preview
	# quotes, so what the plan commits to is what the preview promised.
	_lifts = _lift_profile(protocol, _entry_stage_of(protocol))
	stick_weeks = (cint(_lifts[0]["weeks_after"]) if _lifts
	               else cint(protocol.sticking_to_planting_weeks) or 0)
	turnaround = cint(protocol.turnaround_weeks)
	today = getdate(nowdate())
	# The protocol states the minimum as net area; it has already rounded that up
	min_beds = cint(protocol.min_planting_beds_derived) or 1
	calendar = BlockCalendar(plan.farm, plan.variety)
	block_capacity = calendar.capacity()
	not_placed = 0

	# ---- the planting programme
	tolerance = flt(frappe.db.get_single_value(
		"Summer Flower Settings", "weekly_variance_tolerance_pct"))
	band = (tolerance if tolerance > 0 else 10.0) / 100.0
	week_demand = [demand_map.get((y, w), 0) for (y, w, _m) in grid]
	week_standing = [production[(y, w)] for (y, w, _m) in grid]
	# A cohort is not planted to the plant: it is rounded up to the protocol's
	_b, step, _a = size_planting(protocol, 1, min_beds)
	plan.weekly_variance_band_pct = tolerance if tolerance > 0 else 10.0

	window_start = grid[0][2]
	if programme is not None:
		# Dated by the lifts: each wave of each consignment is its own planting,
		# in the week it actually becomes a plant the field can take.
		cohorts = {}
		for at, plants in programme:
			if not cint(plants):
				continue
			first_cut = getdate(at) + datetime.timedelta(weeks=anchor)
			off = (first_cut - window_start).days // 7
			cohorts[off] = cint(cohorts.get(off, 0)) + cint(plants)
	elif not supply_is_agreed(plan) and not _cut_from_a_pool(protocol):
		# Nothing is planted until the supply is agreed. The demand and what is
		# already standing are still worked out, so the gap this plan has to
		# close is on the page before a single supplier has been chosen.
		#
		# Except where the crop is cut from a pool we already own. Asters are
		# bought as TC ONCE, built up through multiplication and cut from our
		# own mother stock from then on: there is no consignment to wait for,
		# and what paces the planting is how much the pool can give in a week,
		# not how often a house comes. Gating those on a supply agreement left
		# them unplantable for ever.
		cohorts = {}
	else:
		cohorts, _solved = plan_cohorts(rates, week_demand, week_standing,
		                                band=band,
		                                rounding_plants=max(1, cint(step)))
	for idx, (start, wanted_plants) in enumerate(sorted(cohorts.items())):
		if idx >= MAX_NEW_PLANTINGS:
			break
		# `start` is the window week this cohort first cuts in, and it may be
		first_cut = window_start + datetime.timedelta(weeks=start)
		planting_date = first_cut - datetime.timedelta(weeks=anchor)
		p_year, p_week = iso_year_week(planting_date)
		fh_year, fh_week = iso_year_week(first_cut)
		sticking_date = planting_date - datetime.timedelta(weeks=stick_weeks)
		s_year, s_week = iso_year_week(sticking_date)
		uproot = planting_date + datetime.timedelta(weeks=life_weeks)
		# The block is not free the day the crop comes out: it is fallow, sterilised
		released = uproot + datetime.timedelta(weeks=turnaround)

		# The ground is sized for what goes INTO it, not for what is left
		to_plant = plants_to_plant(protocol, wanted_plants)
		beds, planted, _area = size_planting(protocol, to_plant, min_beds)
		if not planted:
			planted = to_plant
		# And what that planting leaves standing, which is what flushes, what the
		plants = plants_standing_from(protocol, planted, wanted_plants)
		# What the market needs is not trimmed to fit the ground. A planting larger
		oversize = bool(block_capacity and beds > block_capacity)

		# Which block a planting goes in is a decision, not an arithmetic result: a
		block = None
		if not calendar.place(beds, planting_date, released, reserve=True):
			not_placed += 1

		family = set()
		mine = defaultdict(int)
		for off, spp in curve:
			hd = planting_date + datetime.timedelta(weeks=cint(off))
			if hd > uproot:
				break
			hy, hw = iso_year_week(hd)
			family.add(hw)
			if (hy, hw) in index:
				stems = int(round(flt(spp) * plants * keep))
				production[(hy, hw)] += stems
				mine[(hy, hw)] += stems
				if block:
					placeable[(hy, hw)] += stems
				contributors[(hy, hw)].append(
					f"new {p_year}-W{p_week:02d} ({beds} beds)"
					+ ("" if block else " — no block yet"))

		net_ha = planting_area_ha(protocol, plants, beds)
		footprints.append((planting_date, uproot, net_ha))

		note = (_("Needs {0} beds, more than any single block here holds ({1}); "
		          "it will be split when blocks are allocated.").format(beds, block_capacity)
		        if oversize else None)
		plan.append("plan_blocks", {
			"is_new_planting": 1,
			"block": block or None,
			"beds": beds,
			"plants": plants,
			"plants_to_plant": planted,
			"plants_required": wanted_plants,
			"sticking_year": s_year,
			"sticking_week": s_week,
			"planting_year": p_year,
			"planting_week": p_week,
			"planting_date": planting_date,
			# Only where the crop is pinched. Zero weeks is not "pinch on the day
			# it is planted", it is a crop nobody pinches, and the plan printed
			# the planting date in a Pinch column for every one of them.
			"pinch_date": (planting_date + datetime.timedelta(
				weeks=cint(protocol.weeks_to_pinch))
				if cint(protocol.weeks_to_pinch) else None),
			"first_harvest_year": fh_year,
			"first_harvest_week": fh_week,
			"harvest_week_family": week_family_label(family),
			"net_area_ha": net_ha,
			"lifetime_stems": int(round(
				(protocol.total_stems_per_plant_life or 0) * plants
			)),
			"below_minimum": 1 if beds < min_beds else 0,
			"not_placed": 0 if block else 1,
			"planting_in_past": 1 if planting_date < today else 0,
			"cuts_before_window": 1 if start < 0 else 0,
			"notes": note if block else (
				_("No block assigned yet. {0} has no summer flower blocks at all."
				  ).format(plan.farm) if not calendar.block_count() else
				_("No block assigned yet: every block of {0} beds or more is held by "
				  "another planting for part of {1} to {2}. The plan counts it; "
				  "assign a block before it can be planted."
				  ).format(beds, planting_date, uproot)
			),
		})
		per_row.append({"weeks": dict(mine), "uproot": str(uproot)})

	# What the season is still short of, after everything the programme plants.
	unmet = sum(max(0, demand_map.get((y, w), 0) - production[(y, w)])
	            for (y, w, _m) in grid)

	# ---- weekly grid
	plan.plantings_not_placed = not_placed
	plan.unmet_stems = unmet
	plan.blocks_used = calendar.used()

	# Beds required over a three-year horizon is a lifetime total: a block that
	occupied = []
	for row in plan.plan_blocks:
		if not row.get("is_new_planting"):
			continue
		if not row.get("planting_date"):
			continue
		start = getdate(row.planting_date)
		occupied.append((start,
		                 start + datetime.timedelta(weeks=life_weeks),
		                 cint(row.beds)))
	for pl in standing_plantings(plan.farm, plan.variety):
		occupied.append((getdate(pl.planting_date), pl.end_date(), cint(pl.beds)))
	peak_beds = 0
	peak_when = None
	for (_y, _w, monday) in grid:
		here = sum(b for s, e, b in occupied if s <= monday < e)
		if here > peak_beds:
			peak_beds, peak_when = here, monday
	plan.peak_concurrent_beds = peak_beds
	plan.peak_beds_week = "%s-W%02d" % iso_year_week(peak_when) if peak_when else None

	# Stow the matrix next to the rows it describes. plan_blocks and this list are
	plan.matrix_json = frappe.as_json({
		"grid": [[y, w, str(m)] for (y, w, m) in grid],
		"rows": [{"weeks": {"%s-%s" % k: v for k, v in r["weeks"].items()},
		          "uproot": r["uproot"]}
		         for r in per_row],
	})

	plan.plan_weeks = []
	running = 0
	outside = 0
	for (year, week, monday) in grid:
		prod = production[(year, week)]
		dem = demand_map.get((year, week), 0)
		running += prod - dem
		# Counted from the plan's own rows rather than taken from the solver.
		if dem and abs(prod - dem) > dem * band + 1:
			outside += 1
		plan.append("plan_weeks", {
			"year": year,
			"week_no": week,
			"week_start_date": monday,
			"month_name": MONTH_NAMES[monday.month - 1],
			"production_stems": prod,
			"placeable_production_stems": placeable[(year, week)],
			"demand_stems": dem,
			"variance_stems": prod - dem,
			"cumulative_variance": running,
			"area_ha": sum(a for s, e, a in footprints if s <= monday <= e),
			"contributing_plantings": "\n".join(contributors[(year, week)]) or None,
		})
	plan.weeks_outside_band = outside


class BlockCalendar:
	"""Which beds are free, in which block, and when."""

	def __init__(self, farm, variety=None):
		self.blocks = []
		rows = frappe.get_all(
			"Block", filters={"farm": farm, "custom_is_summer_flower_block": 1},
			fields=["name", "custom_total_beds"],
			order_by="custom_total_beds desc")
		for r in rows:
			self.blocks.append({"name": r.name, "beds": cint(r.custom_total_beds),
			                    "busy": []})
		self._seed_standing(variety)
		self._seed_history()

	def _seed_history(self):
		"""The last crop off each block, standing or finished."""
		names = [b["name"] for b in self.blocks]
		if not names:
			return
		prior = {}
		for r in frappe.get_all(
				"Planting Calendar", filters={"block": ["in", names]},
				fields=["block", "variety", "planting_date", "planned_uproot_date",
				        "actual_uproot_date", "calendar_status"],
				order_by="planting_date desc"):
			prior.setdefault(r.block, []).append(r)
		for b in self.blocks:
			rows = prior.get(b["name"]) or []
			b["last"] = rows[0] if rows else None
			b["history"] = [{"variety": x.variety, "planted": str(x.planting_date or ""),
			                 "out": str(x.actual_uproot_date or x.planned_uproot_date
			                            or ""),
			                 "status": x.calendar_status}
			                for x in rows[:3]]

	def _seed_standing(self, variety):
		"""Whatever already claims a block holds it until it comes out."""
		f = {"calendar_status": ["in", RESERVING_STATES]}
		for r in frappe.get_all(
				"Planting Calendar", filters=f,
				fields=["name", "block", "beds", "variety", "planting_date",
				        "planned_uproot_date", "actual_uproot_date",
				        "calendar_status"]):
			b = self._get(r.block)
			if not b or not r.planting_date:
				continue
			end = r.actual_uproot_date or r.planned_uproot_date
			b["busy"].append({
				"start": getdate(r.planting_date),
				"end": getdate(end) if end else getdate(r.planting_date),
				"beds": cint(r.beds) or b["beds"],
				"holder": r.name,
				"variety": r.variety,
				"status": r.calendar_status,
				# Whether the crop is in the ground decides what freeing the beds
				"in_ground": r.calendar_status in ("Planted",),
			})

	def _get(self, name):
		for b in self.blocks:
			if b["name"] == name:
				return b
		return None

	@staticmethod
	def _peak_reserved(block, start, end):
		"""The most beds reserved at any single moment inside a window."""
		edges = sorted({start, end}
		               | {r["start"] for r in block["busy"] if start <= r["start"] < end}
		               | {r["end"] for r in block["busy"] if start < r["end"] <= end})
		peak = 0
		for point in edges:
			if point >= end:
				continue
			at = sum(r["beds"] for r in block["busy"]
			         if r["start"] <= point < r["end"])
			peak = max(peak, at)
		return peak

	def free_beds(self, block, start, end):
		"""Beds free for the whole of a window, in one block."""
		return max(0, block["beds"] - self._peak_reserved(block, start, end))

	def place(self, beds, start, end, reserve=True):
		"""The block that fits with the least room to spare."""
		best = None
		for b in self.blocks:
			free = self.free_beds(b, start, end)
			if free < beds:
				continue
			if best is None or free < self.free_beds(best, start, end):
				best = b
		if best is None:
			return None
		if reserve:
			best["busy"].append({"start": start, "end": end, "beds": beds,
			                     "holder": None, "status": "Proposed",
			                     "in_ground": False})
		return best["name"]

	def reserve(self, block, start, end, beds):
		"""Hold beds in a named block, for a choice already made."""
		b = self._get(block)
		if b:
			b["busy"].append({"start": start, "end": end, "beds": beds,
			                  "holder": None, "status": "Proposed",
			                  "in_ground": False})

	def candidates(self, beds, start, end, limit=6):
		"""Every block, ranked: the ones that fit, then the ones that nearly do."""
		out = []
		for b in self.blocks:
			free = self.free_beds(b, start, end)
			# What is standing there, whether or not there is room for more. A block
			on_it = [{"planting": r["holder"], "variety": r.get("variety"),
			          "beds": r["beds"], "frees_on": str(r["end"]),
			          "in_ground": r["in_ground"]}
			         for r in sorted(b["busy"], key=lambda x: x["end"])
			         if not (end <= r["start"] or start >= r["end"])]
			if free >= beds:
				out.append({"block": b["name"], "total_beds": b["beds"],
				            "free_beds": free, "fits": True, "free_from": None,
				            "weeks_late": 0, "blockers": [],
				            "standing": on_it, "history": b.get("history") or []})
				continue
			# What holds it, and when it would be free enough.
			clash = [r for r in b["busy"]
			         if not (end <= r["start"] or start >= r["end"])]
			free_from = None
			for r in sorted(clash, key=lambda x: x["end"]):
				if b["beds"] - self._peak_reserved(b, r["end"], end) >= beds:
					free_from = r["end"]
					break
			out.append({
				"block": b["name"], "total_beds": b["beds"], "free_beds": free,
				"fits": False,
				"free_from": str(free_from) if free_from else None,
				"weeks_late": ((free_from - start).days // 7) if free_from else None,
				"blockers": [{"planting": r["holder"], "status": r["status"],
				              "beds": r["beds"], "frees_on": str(r["end"]),
				              "in_ground": r["in_ground"],
				              "uproot_weeks_early": max(
					              0, (r["end"] - start).days // 7)}
				             for r in sorted(clash, key=lambda x: x["end"])[:3]],
				"standing": on_it,
				"history": b.get("history") or [],
			})
		out.sort(key=lambda c: (not c["fits"],
		                        c["free_beds"] - beds if c["fits"] else 0,
		                        c["weeks_late"] if c["weeks_late"] is not None else 9999))
		return out[:limit]

	def capacity(self):
		return max((b["beds"] for b in self.blocks), default=0)

	def block_count(self):
		"""How many blocks exist at all, before anything is booked."""
		return len(self.blocks)

	def used(self):
		return len([b for b in self.blocks if b["busy"]])

	def get(self, name):
		return self._get(name)


@frappe.whitelist()
def build_all(season_start_year=None, farm=None, dry_run=1, rebuild=0):
	"""A plan for every variety the market asks for, each on its own protocol."""
	dry_run, rebuild = cint(dry_run), cint(rebuild)
	filters = {"farm": farm} if farm else {}
	rows = []
	for reg in frappe.get_all("Summer Flower Market Demand", filters=filters,
	                          fields=["name", "variety", "farm"], order_by="variety"):
		out = {"register": reg.name, "variety": reg.variety, "farm": reg.farm,
		       "cycle": None, "plan": None, "status": None, "beds": 0, "plants": 0,
		       "plantings": 0}
		try:
			demand = frappe.get_doc("Summer Flower Market Demand", reg.name)
			if not demand.demand_weeks:
				out["status"] = _("register has no weeks")
				rows.append(out); continue
			first = demand.demand_weeks[0]
			season = season_year(season_start_year) or season_for(
				first.week_start_date or iso_monday(cint(first.year), cint(first.week_no)))

			existing = frappe.get_all(
				"Summer Flower Production Plan",
				filters={"market_demand": reg.name, "season_start_year": season,
				         "docstatus": ["<", 2]},
				fields=["name"], limit_page_length=1)
			if existing and not rebuild:
				out.update(plan=existing[0].name, status=_("already planned"))
				rows.append(out); continue

			plan = frappe.new_doc("Summer Flower Production Plan")
			plan.market_demand = reg.name
			if reg.farm:
				plan.farm = reg.farm
			plan.season_start_year = season
			plan.pull_header_from_demand()
			plan.set_season()
			plan.resolve_protocol()
			out["cycle"] = frappe.db.get_value(
				"Crop Protocol Version", plan.protocol, "growing_cycle")

			_populate(plan)
			new = [b for b in plan.plan_blocks if b.is_new_planting]
			out.update(plantings=len(new),
			           beds=sum(cint(b.beds) for b in new),
			           plants=sum(cint(b.plants) for b in new))
			if dry_run:
				out["status"] = _("would build")
			else:
				plan.insert()
				out.update(plan=plan.name, status=_("built"))
		except Exception as e:
			# One protocol that cannot project must not cost the other plans.
			out["status"] = "%s: %s" % (type(e).__name__,
			                            frappe.utils.strip_html(str(e))[:140])
		rows.append(out)

	if not dry_run:
		frappe.db.commit()
	return {"dry_run": bool(dry_run), "season": season_year(season_start_year) or None,
	        "rows": rows}


def report_build_all(season_start_year=None, farm=None, dry_run=1, rebuild=0):
	"""Readable build_all(), for the console."""
	r = build_all(season_start_year=season_start_year, farm=farm,
	              dry_run=dry_run, rebuild=rebuild)
	print("DRY RUN -- nothing created\n" if r["dry_run"] else "BUILT\n")
	print("%-40s %-16s %-28s %6s %6s %9s  %s" % (
		"variety", "farm", "cycle", "plants", "beds", "plants", "status"))
	for x in r["rows"]:
		print("%-40s %-16s %-28s %6s %6s %9s  %s" % (
			(x["variety"] or "")[:40], (x["farm"] or "-")[:16],
			(x["cycle"] or "-")[:28], x["plantings"], x["beds"], x["plants"],
			x["status"]))
	ok = [x for x in r["rows"] if x["status"] in ("would build", "built")]
	print("\n%d of %d registers planned" % (len(ok), len(r["rows"])))
	return r
