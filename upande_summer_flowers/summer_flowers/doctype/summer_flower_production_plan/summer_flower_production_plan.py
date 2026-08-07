# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt

import datetime
import math
from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, getdate, nowdate

from upande_summer_flowers.summer_flowers.doctype.crop_protocol_version.crop_protocol_version import (
	current_version,
)
from upande_summer_flowers.summer_flowers.doctype.planting_calendar.planting_calendar import (
	RESERVING_STATES,
	production_by_week,
	standing_plantings,
)
from upande_summer_flowers.summer_flowers.planning import (
	MONTH_NAMES,
	iso_monday,
	iso_year_week,
	week_sequence,
)

# Stop the greedy filler running away if a protocol is misconfigured.
MAX_NEW_PLANTINGS = 400


class SummerFlowerProductionPlan(Document):
	def validate(self):
		self.pull_header_from_demand()
		self.set_season()
		self.set_period_end()
		self.roll_up_months()
		self.set_totals()
		self.check_protocol_freshness()
		self.sync_status()

	def on_submit(self):
		"""Approval is the workflow transition that submits the plan.

		Approving a plan is the moment it becomes a commitment, so everything that
		follows from that commitment is created here: the budget, and the propagation
		plan that says where the cuttings come from. The propagation plan had to be
		asked for separately, which is why an approved plan could sit beside "Not
		created, so nothing knows where the cuttings come from" -- the one thing with
		a two year lead time, waiting on somebody to press a second button.
		"""
		self.db_set("status", "Approved")
		self.create_budget()
		self.ensure_propagation()

	def ensure_propagation(self):
		"""Create or rebuild the propagation plan for this crop and season.

		Never fatal. A plan that cannot source its cuttings is still an approved plan
		and the budget is already posted; the chain says so plainly on the propagation
		step, which is more use than an exception that leaves the approval half done.
		"""
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
				_("The plan is approved and the budget is posted, but its propagation "
				  "plan could not be built. Open the Propagation step to see why."),
				indicator="orange", title=_("Propagation not built"))
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
		"""What the market wants comes from the demand. What the crop does does not.

		The demand register used to carry a protocol version too, which meant the
		same fact was stored twice and could disagree with itself. The version is
		an assumption of *this plan* -- density, cycle length, flush curve -- so it
		is chosen here, and changing it is a change to the plan that Regenerate
		then acts on.
		"""
		if not self.market_demand:
			return
		d = frappe.get_cached_doc("Summer Flower Market Demand", self.market_demand)
		self.variety = d.variety
		# The farm is the plan's, not the register's. One variety's demand can be met
		# from several farms, each with its own protocol and its own blocks, so the
		# farm is chosen here and the protocol resolves against it.
		if not self.farm:
			frappe.throw(_("Choose the farm this plan is grown on. The demand is for "
			               "the variety; the plan is what a farm commits to."))
		# The demand register is authoritative. Do not fall back to whatever
		# frappe.new_doc pre-filled from the global default company -- on a
		# multi-company site that silently plans against the wrong entity.
		self.company = d.company or self.company
		self.currency = d.currency or self.currency
		if not self.price_per_stem:
			self.price_per_stem = d.price_per_stem
		self.resolve_protocol()

	def resolve_protocol(self):
		"""Default to the version in force, and refuse another crop's protocol."""
		if not self.protocol:
			self.protocol = current_version(self.variety, self.farm)
			if not self.protocol:
				frappe.throw(_(
					"No Active Crop Protocol Version for {0} at {1}. Approve one "
					"before planning against this demand."
				).format(self.variety, self.farm))

		v = frappe.get_cached_doc("Crop Protocol Version", self.protocol)
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
			# time the document was written is the best available lower bound on
			# when its rows were built, so adopt it once instead of accusing every
			# older plan of being stale. Read it from the database: by the time
			# validate runs, self.modified has already been moved to now.
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
		"""The plan covers one growing season: 1 July to 30 June.

		This is the farm's season, deliberately not an ERPNext Fiscal Year. Those are
		calendar years here and the accounts use them; creating July-to-June ones
		beside them would leave every posting date resolving to two fiscal years,
		which ERPNext refuses. The budget still posts against whichever calendar
		fiscal year each month falls in, so accounting is untouched.

		The weekly grid is derived from the season: the ISO week containing 1 July
		through the one containing 30 June, which is 52 weeks most years and 53 when
		the ISO calendar says so.
		"""
		year = cint(self.season_start_year)
		if not year:
			# Plans made before the season existed keep the grid they were built on
			# rather than being silently re-scoped.
			if self.from_year and self.from_week:
				self.season = _("{0} weeks from {1}-W{2:02d}").format(
					cint(self.weeks_covered), self.from_year, cint(self.from_week))
			return

		start = datetime.date(year, 7, 1)
		end = datetime.date(year + 1, 6, 30)
		self.season = "%s-%s" % (year, str(year + 1)[2:])
		self.season_start_date = start
		self.season_end_date = end

		# A week belongs to the season its MONDAY falls in. Taking the week that
		# CONTAINS 1 July and the one that contains 30 June counts the boundary week
		# twice -- 30 June 2027 and 1 July 2027 are both in 2027-W26 -- which made
		# every season 53 weeks and gave consecutive seasons a week in common.
		first_monday = start + datetime.timedelta(days=(7 - start.weekday()) % 7)
		next_start = datetime.date(year + 1, 7, 1)
		last_monday = next_start + datetime.timedelta(days=(7 - next_start.weekday()) % 7) \
			- datetime.timedelta(weeks=1)
		self.from_year, self.from_week = iso_year_week(first_monday)
		self.weeks_covered = ((last_monday - first_monday).days // 7) + 1

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
		"""Recount what has a block, and what that grows, from the rows as they stand.

		plantings_not_placed, unmet_stems and the weekly placeable figures were only
		computed during a rebuild, so assigning a block by hand left them saying what
		was true before: 34 plantings unassigned and the counter still reading 0.

		The per-planting weekly stems are already in matrix_json, row for row with
		plan_blocks, so the placeable grid is those rows that have a block. No yield is
		recalculated here -- the same numbers, added up differently.
		"""
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
		# plan; the second is a constraint report on it.
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
		# are the beds to prepare and the cuttings to stick: the TC order goes to the
		# lab long before anyone knows which block will be free, and the propagation
		# plan raises cuttings for the whole plan on the same reasoning.
		new_rows = [b for b in self.plan_blocks if b.is_new_planting]
		self.new_beds_required = sum((b.beds or 0) for b in new_rows)
		self.new_plants_required = sum((b.plants or 0) for b in new_rows)

		# Motherstock is sized by the biggest single sticking week, never the annual
		# total -- cuttings cannot be banked.
		sticking = defaultdict(int)
		for b in new_rows:
			if b.sticking_year and b.sticking_week:
				sticking[(b.sticking_year, b.sticking_week)] += b.plants or 0
		if sticking:
			peak_key = max(sticking, key=lambda k: sticking[k])
			self.peak_weekly_sticking = sticking[peak_key]
			self.peak_sticking_week = f"{peak_key[0]}-W{peak_key[1]:02d}"
		else:
			self.peak_weekly_sticking = 0
			self.peak_sticking_week = None

		# The same peak, kept under its own name because the TC order and the
		# propagation plan both read it.
		planned_sticking = defaultdict(int)
		for b in self.plan_blocks:
			if not b.is_new_planting:
				continue
			if b.sticking_year and b.sticking_week:
				planned_sticking[(b.sticking_year, b.sticking_week)] += b.plants or 0
		if planned_sticking:
			key = max(planned_sticking, key=lambda k: planned_sticking[k])
			self.peak_weekly_sticking_planned = planned_sticking[key]
			self.peak_sticking_week_planned = "%s-W%02d" % key
		else:
			self.peak_weekly_sticking_planned = 0
			self.peak_sticking_week_planned = None
		self.set_tc_order(planned_sticking)
		self.set_space()

	# --------------------------------------------------------------------- space
	def set_space(self):
		"""What this plan needs against what the farm has, in beds and hectares.

		Two capacity columns, deliberately, because they disagree. The stated figure
		is what each block claims through its area history; the measured one is the
		sum of its beds' own length and width. On this site most blocks have no beds
		pointing at them at all, so the measured column reads low -- which is a
		statement about the bed register, not about the land, and saying so is more
		use than averaging the two into one number nobody can act on.

		The planner already refuses a planting it cannot place, so
		total_production_stems is what the space allows. This section is why.
		"""
		self.plan_space = []
		self.space_required_ha = 0
		self.space_stated_ha = 0
		self.space_measured_ha = 0
		self.beds_required_peak = cint(self.peak_concurrent_beds)
		self.beds_available_stated = 0
		self.beds_available_measured = 0
		self.space_utilisation_pct = 0
		self.space_verdict = None
		if not self.farm:
			return

		v = getattr(self, "_version", None)
		sqm_per_bed = flt(getattr(v, "sqm_net_per_bed", 0)) if v else 0
		self.space_required_ha = (self.beds_required_peak * sqm_per_bed) / 10_000

		blocks = frappe.get_all(
			"Block", filters={"farm": self.farm, "custom_is_summer_flower_block": 1},
			fields=["name", "custom_total_beds", "custom_net_area_ha",
			        "custom_measured_beds", "custom_measured_net_area_ha"],
			order_by="name asc")

		# What this plan itself asked of each block, and what someone else holds.
		mine = defaultdict(int)
		for row in self.plan_blocks:
			if row.get("block") and row.get("is_new_planting"):
				mine[row.block] = max(mine[row.block], cint(row.beds))
		others = defaultdict(int)
		for pl in standing_plantings(self.farm, self.variety):
			others[pl.block] += cint(pl.beds)

		for b in blocks:
			stated_beds = cint(b.custom_total_beds)
			needed = mine.get(b.name, 0)
			held = others.get(b.name, 0)
			free = max(0, stated_beds - held)
			self.space_stated_ha += flt(b.custom_net_area_ha)
			self.space_measured_ha += flt(b.custom_measured_net_area_ha)
			self.beds_available_stated += stated_beds
			self.beds_available_measured += cint(b.custom_measured_beds)
			self.append("plan_space", {
				"block": b.name,
				"beds_stated": stated_beds,
				"beds_measured": cint(b.custom_measured_beds),
				"area_stated_ha": flt(b.custom_net_area_ha),
				"area_measured_ha": flt(b.custom_measured_net_area_ha),
				"beds_needed_by_plan": needed,
				"beds_held_by_others": held,
				"beds_free": free,
				"verdict": (
					_("{0} beds to this plan").format(needed) if needed else
					_("full -- held by another planting") if held and not free else
					_("{0} beds free").format(free) if free else
					_("no beds stated")
				),
			})

		self.space_utilisation_pct = (
			self.space_required_ha / self.space_stated_ha * 100
			if self.space_stated_ha else 0)

		# Land at this farm that belongs to no block. A bed sits under a block or
		# directly under a greenhouse, never both, so this is the rest of the farm --
		# and it is where the answer to "we have no space" usually is. Chepsito has no
		# blocks at all and 14.4 ha of beds, which is a different problem from having
		# no land, and needs a different thing done about it.
		unblocked = frappe.db.sql('''
			select count(*) n, ifnull(sum(b.bed_area), 0) sqm
			from tabBed b join tabWarehouse w on w.name = b.greenhouse
			where w.custom_farm = %s and ifnull(b.custom_block, '') = ''
		''', self.farm, as_dict=True)[0]
		self.beds_unblocked = cint(unblocked.n)
		self.space_unblocked_ha = flt(unblocked.sqm) / 10_000

		notes = []
		if not blocks and self.beds_unblocked:
			notes.append(_(
				"{0} has no summer flower blocks, so nothing can be placed there -- but "
				"it does have {1} beds ({2} ha) in its greenhouses belonging to no "
				"block. The land is there; draw blocks over it and this plan can be "
				"planted."
			).format(self.farm, self.beds_unblocked, round(self.space_unblocked_ha, 3)))
		elif not blocks:
			notes.append(_(
				"{0} has no summer flower blocks and no beds in its greenhouses, so it "
				"has no space to plan into at all."
			).format(self.farm))
		elif self.space_utilisation_pct > 100:
			notes.append(_(
				"This plan needs {0} ha at its peak and {1} has {2} ha, so it cannot "
				"fit however the blocks are arranged. {3} plantings are waiting on a "
				"block and some of them will not get one until the farm has more land."
			).format(round(self.space_required_ha, 3), self.farm,
			         round(self.space_stated_ha, 3), cint(self.plantings_not_placed)))
		elif cint(self.plantings_not_placed):
			notes.append(_(
				"There is enough land in total ({0} ha needed of {1} ha) but {2} "
				"plantings are still waiting on a block: a block is held for a whole "
				"planting's life, so the free beds are not free at the same time. "
				"Moving a planting a week or two either way often finds one."
			).format(round(self.space_required_ha, 3),
			         round(self.space_stated_ha, 3), cint(self.plantings_not_placed)))
		# Its own note, not part of the chain above. Land the plan cannot reach is
		# worth saying, but never at the price of suppressing the reason plantings
		# failed -- that line is the one a planner acts on.
		if blocks and self.beds_unblocked:
			notes.append(_(
				"A further {0} beds ({1} ha) at this farm belong to no block, so the "
				"plan cannot reach them."
			).format(self.beds_unblocked, round(self.space_unblocked_ha, 3)))
		if self.beds_available_measured < self.beds_available_stated:
			notes.append(_(
				"The bed register accounts for {0} of the {1} beds these blocks claim. "
				"Planning uses the stated figure; until the beds are linked to their "
				"blocks the measured column is not a second opinion, it is a gap."
			).format(self.beds_available_measured, self.beds_available_stated))
		if not sqm_per_bed:
			notes.append(_("The protocol does not state a net area per bed, so the "
			               "hectares this plan needs cannot be computed."))
		self.space_verdict = "\n".join(notes) or _(
			"{0} ha of {1} ha, {2} beds of {3} at the peak."
		).format(round(self.space_required_ha, 3), round(self.space_stated_ha, 3),
		         self.beds_required_peak, self.beds_available_stated)

	# ---------------------------------------------------------------- TC to order
	def set_tc_order(self, planned_sticking):
		"""What to buy, on the document that decides it.

		The number was only ever on the dashboard, so a plan could be read, approved
		and handed over without the one purchase it depends on appearing anywhere on
		it. The arithmetic is the protocol version's own -- cuttings_for_plants and
		tc_plants_for -- so this is the same figure the dashboard shows, not a second
		opinion about it.
		"""
		self.tc_plants_to_order = 0
		self.tc_mother_plants = 0
		self.tc_cuttings_at_peak = 0
		self.tc_order_by_date = None
		self.tc_status = None

		v = getattr(self, "_version", None)
		peak = cint(self.peak_weekly_sticking_planned)
		if not v or not peak:
			self.tc_status = _("No plantings proposed, so nothing to order.")
			return

		cuttings = cint(v.cuttings_for_plants(peak))
		per_week = flt(v.cuttings_per_plant_per_week) or 1.0
		mothers = int(math.ceil(cuttings / per_week)) if cuttings else 0
		cycles = cint(v.max_multiplication_cycles)
		self.tc_cuttings_at_peak = cuttings
		self.tc_mother_plants = mothers
		self.tc_plants_to_order = int(math.ceil(v.tc_plants_for(mothers, cycles))) \
			if mothers else 0

		# Working back from the first sticking week, through the one function that
		# knows how: the dashboard used to do this arithmetic itself and disagreed by
		# nine months.
		from upande_summer_flowers.summer_flowers.doctype.summer_flower_motherstock_batch.summer_flower_motherstock_batch import (
			tc_order_by_date,
		)

		first = min(planned_sticking) if planned_sticking else None
		if first:
			self.tc_order_by_date = tc_order_by_date(v, iso_monday(first[0], first[1]))

		notes = []
		if self.tc_order_by_date and self.tc_order_by_date < getdate(nowdate()):
			notes.append(_(
				"The order date has already passed ({0}), so the first sticking week "
				"{1}-W{2:02d} cannot be met from a new TC order. Buy rooted cuttings "
				"for the early weeks or move the demand later."
			).format(self.tc_order_by_date, first[0], first[1]))
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
		"""Work out how this plan's cuttings get sourced.

		Separate from the plan because sourcing is a decision -- what existing
		motherstock covers, what has to be bought as TC -- and it is reviewed and
		approved on its own before any money is committed.
		"""
		from upande_summer_flowers.summer_flowers.doctype \
			.summer_flower_propagation_plan.summer_flower_propagation_plan import (
				build_from_plan,
			)

		# as_dict so the caller can say whether it created or updated, and what moved.
		# There is one propagation plan per variety per season, so a second production
		# plan for the same crop and season rebuilds it instead of starting a rival.
		return build_from_plan(self.name, as_dict=True)

	@frappe.whitelist()
	def create_plantings(self):
		"""Turn approved new-planting rows into actual Plantings on the ground.

		Rows without a block are skipped -- they need allocating first. Rows already
		converted are skipped too, so this is safe to run more than once.
		"""
		if self.docstatus != 1:
			frappe.throw(_("Approve the plan before creating plantings."))

		created, skipped = [], []
		for row in self.plan_blocks:
			if not row.is_new_planting:
				continue
			# A link to a planting that has since been deleted must not block a
			# rebuild, otherwise the row is skipped silently and forever.
			if row.existing_planting:
				if frappe.db.exists("Planting Calendar", row.existing_planting):
					continue
				row.db_set("existing_planting", None)
			if not row.block:
				skipped.append(_("row {0}: no block allocated").format(row.idx))
				continue

			doc = frappe.get_doc({
				"doctype": "Planting Calendar",
				"block": row.block,
				"variety": self.variety,
				"crop_protocol_version": self.protocol,
				"company": self.company,
				"beds": row.beds,
				"planting_date": row.planting_date,
				"calendar_status": "Draft",
				"workflow_state": "Draft",
			})
			try:
				doc.insert()
			except frappe.ValidationError as e:
				# Most often the block is already occupied for that window.
				skipped.append(_("row {0}: {1}").format(
					row.idx, frappe.utils.strip_html(str(e))[:120]
				))
				continue
			row.db_set("existing_planting", doc.name)
			created.append(doc.name)

		return {"created": created, "skipped": skipped}

	@frappe.whitelist()
	def regenerate(self, adopt_current_protocol=1):
		"""Rebuild the weekly grid and planting proposals against the current protocol.

		Regenerate used to rebuild against the version the plan was already pinned to,
		which meant a protocol change reached nothing: the dashboard said "protocol
		changed since this plan was built -- regenerate to apply" and regenerating did
		not apply it. It adopts the version in force now, and says which version it
		moved from, because a silent switch of the numbers a plan rests on is worse
		than not switching at all.

		An existing propagation plan is rebuilt too. It sources the cuttings this plan
		needs, and leaving it on the old figures is how a plan and its own sourcing end
		up describing different crops. A propagation plan is not created here: that
		happens when the plan is approved, because sourcing an unapproved plan commits
		nobody to anything.
		"""
		if self.docstatus != 0:
			frappe.throw(_("Only a draft plan can be regenerated."))
		was = self.protocol
		if cint(adopt_current_protocol):
			current = current_version(self.variety, self.farm)
			if current:
				self.protocol = current
		_populate(self)
		self.save()
		if was and was != self.protocol:
			frappe.msgprint(
				_("Rebuilt on {0}, which is the version in force. It was built on {1}."
				  ).format(self.protocol, was), indicator="blue")

		existing = frappe.db.get_value(
			"Summer Flower Propagation Plan",
			{"variety": self.variety,
			 "season_start_year": cint(self.season_start_year),
			 "status": ["!=", "Rejected"], "docstatus": 0}, "name")
		if existing:
			self.ensure_propagation()
		return self.name


# ---------------------------------------------------------------------------
# Protocol freshness
# ---------------------------------------------------------------------------

def protocol_freshness(plan):
	"""Whether a plan's stored numbers still match the protocol they cite.

	plan_weeks and plan_blocks are stored rows built by Regenerate -- nothing
	recomputes on its own. So editing a protocol value, or activating a newer
	version, leaves the numbers citing a version that no longer says what they
	were built from. Left silent that reads as "I changed the protocol and
	nothing happened", which is how this was found.

	Computed rather than read back from the document because Frappe does not run
	validate on a submitted one, and an approved plan is exactly the plan a
	manager is looking at.
	"""
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

	return {
		"status": status,
		"stale": 1 if notes else 0,
		"in_force": in_force,
		"note": "\n".join(notes + [
			_("Regenerate to rebuild the weekly grid and the planting proposals "
			  "from the protocol as it stands now.")
		]) if notes else None,
	}


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def season_for(date):
	"""The season a date falls in, by its starting year. July starts the season."""
	d = getdate(date)
	return d.year if d.month >= 7 else d.year - 1


@frappe.whitelist()
def block_suggestions(plan, only_unassigned=1):
	"""For each planting, the blocks that could hold it -- nothing is assigned.

	The plan is built from the demand and the protocol; which beds a planting goes
	into is decided here, afterwards, by someone who knows the farm. So this ranks
	the options and names what stands in the way, and assign_block is a separate
	call that the reader makes.
	"""
	frappe.has_permission("Summer Flower Production Plan", "read", plan, throw=True)
	p = frappe.get_doc("Summer Flower Production Plan", plan)
	protocol = frappe.get_cached_doc("Crop Protocol Version", p.protocol)
	life_weeks = cint(protocol.total_weeks_in_ground)
	only_unassigned = cint(only_unassigned)

	# Seeded with what is already reserved, then each row this plan has already
	# assigned is reserved too, so two rows are never offered the same beds.
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
	"""Put one planting in one block, or take it out again.

	Refuses only what is arithmetically impossible -- not enough beds free for the
	planting's whole life -- and says what is in the way when it does. Everything
	else is the planner's call, including leaving a planting unassigned.
	"""
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
	# assigning a block changes that second figure and has to be recomputed. The plan
	# itself does not move: the plantings were always counted.
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
	"""Take every suggestion the calendar would make, in one go.

	The same tightest-fit rule the planner uses, applied to the rows that have no
	block. Offered because doing it by hand for thirty-five plantings is tedious, not
	because it knows better than the person doing it -- anything it assigns can be
	changed afterwards.
	"""
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


@frappe.whitelist()
def plan_preview(market_demand, farm=None, season_start_year=None):
	"""What creating this plan would mean, before anything is written.

	A production plan is the document a budget and a propagation plan hang off, and
	it was created blind: you picked a farm and a year and found out afterwards
	whether the farm had blocks, whether a protocol was in force, whether the demand
	reached into the season at all, or whether a plan for that season already
	existed. All of that is knowable first, so it is shown first.
	"""
	frappe.has_permission("Summer Flower Market Demand", "read", market_demand, throw=True)
	d = frappe.get_cached_doc("Summer Flower Market Demand", market_demand)
	year = cint(season_start_year) or season_for(nowdate())
	start = datetime.date(year, 7, 1)
	end = datetime.date(year + 1, 6, 30)

	weeks = [r for r in d.demand_weeks
	         if r.week_start_date and start <= getdate(r.week_start_date) <= end]
	stems = sum(cint(r.demand_stems) for r in weeks)
	firm = sum(cint(r.demand_stems) for r in weeks if r.is_firm)

	notes, blocking = [], []
	version = current_version(d.variety, farm) if farm else None
	if not farm:
		blocking.append(_("Choose the farm. The demand is for the variety; a plan is "
		                  "what one farm commits to."))
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
			select count(*) n from tabBed b join tabWarehouse w on w.name = b.greenhouse
			where w.custom_farm = %s and ifnull(b.custom_block, '') = ''
		""", farm)[0][0]
		notes.append(_(
			"{0} has no summer flower blocks, so nothing can be placed there and the "
			"plan will show no production. It does have {1} beds in its greenhouses "
			"belonging to no block."
		).format(farm, unblocked) if unblocked else _(
			"{0} has no summer flower blocks and no beds, so there is nowhere to plant."
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
	# for someone to read off a date after the plan is approved.
	lead = None
	if version and weeks:
		v = frappe.get_cached_doc("Crop Protocol Version", version)
		first = min(getdate(r.week_start_date) for r in weeks)
		from upande_summer_flowers.summer_flowers.doctype.summer_flower_motherstock_batch.summer_flower_motherstock_batch import (
			lab_lead_weeks,
		)
		weeks_back = cint(lab_lead_weeks(v)) + cint(v.lead_time_weeks) \
			+ cint(v.first_harvest_offset_weeks) + cint(v.sticking_to_planting_weeks)
		order_by = first - datetime.timedelta(weeks=weeks_back)
		lead = {"first_demand_week": str(first), "weeks_back": weeks_back,
		        "order_by": str(order_by), "late": order_by < getdate(nowdate())}
		if lead["late"]:
			notes.append(_(
				"To harvest in the first demanded week ({0}) the TC order would have "
				"had to be placed by {1} -- {2} weeks earlier, and that date has "
				"passed. The early weeks will be short unless you buy rooted cuttings."
			).format(first, order_by, weeks_back))

	# What it would cost to say yes: the plantlets to buy and the ground to find. Both
	# are knowable from the demand and the protocol before a plan exists, and both are
	# the reason a plan gets abandoned after it is built -- the order that had to go a
	# year ago, or the land that was never there.
	tc = space = None
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
		ppb = cint(v.plants_per_bed) or 1
		min_beds = cint(v.min_planting_beds_derived) or 1
		if first_flush:
			# The busiest week decides the pool, because cuttings cannot be banked.
			beds = max(min_beds, int(math.ceil(peak_stems / (first_flush * ppb))))
			plants = beds * ppb
			cuttings = cint(v.cuttings_for_plants(plants))
			per_week = flt(v.cuttings_per_plant_per_week) or 1.0
			mothers = int(math.ceil(cuttings / per_week))
			tc = {
				# Which week, not just how big. "The peak week" means nothing without
				# it, and it is the week every date downstream is counted back from.
				"peak_week": "%s-W%02d" % (cint(peak_row.year), cint(peak_row.week_no)),
				"peak_week_stems": peak_stems,
				"plants_in_peak_week": plants,
				"cuttings": cuttings,
				"mother_plants": mothers,
				# Carried so the reader can see why mothers and cuttings are often the
				# same number: at one cutting per plant per week they are equal, and
				# printing both without the rate looks like a mistake.
				"cuttings_per_plant_per_week": per_week,
				"plantlets": int(math.ceil(v.tc_plants_for(mothers))),
				"order_by": lead["order_by"] if lead else None,
				"late": bool(lead and lead["late"]),
			}
			# Ground: what has to be STANDING to deliver one season's demand, so the
			# yield has to be per year, not per life. A plant gives 18.8 stems over
			# 2.13 years, not 18.8 in the season -- dividing by the lifetime figure
			# said Aster needed 1.09 ha where it needs 2.33, less than half the land.
			years = flt(v.life_expectancy_years) or 1
			per_year = flt(v.total_stems_per_plant_life) / years
			if per_year:
				total_plants = int(math.ceil(stems / per_year))
				total_beds = int(math.ceil(total_plants / ppb))
				need_ha = total_beds * flt(v.sqm_net_per_bed) / 10_000
				have = frappe.db.sql("""
					select ifnull(sum(custom_net_area_ha), 0) ha,
					       ifnull(sum(custom_total_beds), 0) beds
					from tabBlock where farm = %s and custom_is_summer_flower_block = 1
				""", farm, as_dict=True)[0] if farm else {"ha": 0, "beds": 0}
				space = {
					"basis": "plants standing to deliver one season at %.1f stems "
					         "per plant per year" % per_year,
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
			notes.append(_(
				"About {0} plantlets to buy. They multiply into {1} mother plants, "
				"which is what it takes to cut {2} cuttings in {3} -- the busiest "
				"sticking week, and the one the pool has to be sized for because "
				"cuttings cannot be banked.{4}"
			).format("{:,}".format(tc["plantlets"]),
			         "{:,}".format(tc["mother_plants"]),
			         "{:,}".format(tc["cuttings"]), tc["peak_week"],
			         _(" The order was due {0}.").format(tc["order_by"])
			         if tc["late"] else
			         (_(" Order by {0}.").format(tc["order_by"])
			          if tc["order_by"] else "")))

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
	if farm and not frappe.db.exists("Farm", farm):
		# Caught a year arriving here as the farm once already, from a positional call
		# made before this signature grew. Fail with the value rather than silently
		# looking for a protocol at a farm that cannot exist.
		frappe.throw(_("{0} is not a Farm. Name the farm this plan is grown on.")
		             .format(farm))
	plan = frappe.new_doc("Summer Flower Production Plan")
	plan.market_demand = market_demand
	if farm:
		plan.farm = farm
	# One plan covers one season. Defaults to the season the register opens in, so a
	# three-year register is planned a season at a time rather than in one lump.
	plan.season_start_year = cint(season_start_year) or season_for(
		first.week_start_date or iso_monday(cint(first.year), cint(first.week_no)))
	plan.pull_header_from_demand()
	plan.set_season()

	_populate(plan)
	plan.insert()
	return plan.name


def _populate(plan):
	"""Fill plan_weeks and plan_blocks.

	Production comes first from plantings already standing, then new plantings are
	proposed week by week to close whatever deficit remains. Each proposed planting
	is immediately folded into the grid, so its later flushes (13, 26, 39 weeks on)
	count towards those weeks and we do not plant for them twice.
	"""
	demand = frappe.get_doc("Summer Flower Market Demand", plan.market_demand)
	if not plan.protocol:
		plan.resolve_protocol()
	protocol = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)
	# Watermark the version as it stands at this moment, so a later edit to it can
	# be told apart from one that predates these rows.
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
	# free for it is a separate question, answered later when blocks are assigned, so
	# production counts every planting the plan proposes.
	#
	# It did not. A proposal with no block was excluded, which made a plan at a farm
	# whose blocks were all taken read zero production -- and worse, the loop kept
	# proposing for a week it had already proposed for, because the deficit never
	# closed: forty plantings for a demand that needs thirteen.
	#
	# placeable is the second grid: the same plan restricted to the plantings a block
	# is actually free for. That is what the farm can grow today, and it stays visible
	# beside the plan rather than replacing it.
	placeable = defaultdict(int)
	contributors = defaultdict(list)
	# Every planting's own week-by-week stems, keyed by its plan_blocks row. This is
	# the shape the planning workbook is actually read in -- one row per planting, one
	# column per week -- and it was the one thing the plan could not produce: it held
	# the weekly totals and it held the plantings, but never which planting put which
	# stems in which week. Recorded as the numbers are folded in, so the matrix cannot
	# drift from the plan it came from.
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
				contributors[(y, w)].append(f"{planting.block} ({planting.beds}b)")
				hit = True
		footprints.append((
			getdate(planting.planting_date), planting.end_date(),
			planting.net_area_ha or 0,
		))
		if hit:
			fh_year, fh_week = planting.first_harvest()
			plan.append("plan_blocks", {
				"is_new_planting": 0,
				"block": planting.block,
				"existing_planting": planting.name,
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
		frappe.throw(
			_("Protocol {0} has no flush schedule, so production cannot be projected.").format(
				protocol.name
			)
		)

	stems_f1 = offsets[0][1]
	first_offset = protocol.first_harvest_offset_weeks or offsets[0][0]
	plants_per_bed = protocol.plants_per_bed or 1
	life_weeks = protocol.total_weeks_in_ground or 0
	stick_weeks = protocol.sticking_to_planting_weeks or 0
	today = getdate(nowdate())
	# The protocol states the minimum as net area; it has already rounded that up
	# to whole beds, because a bed is the unit that gets planted.
	min_beds = cint(protocol.min_planting_beds_derived) or 1
	calendar = BlockCalendar(plan.farm, plan.variety)
	block_capacity = calendar.capacity()
	not_placed = unmet = 0

	proposed = 0
	for (year, week, monday) in grid:
		# Against the plan. Every proposal counts towards it, so a week is proposed for
		# once and the loop converges on the plantings the demand needs rather than
		# retrying weeks whose blocks were full.
		deficit = demand_map.get((year, week), 0) - production[(year, week)]
		if deficit <= 0:
			continue
		if proposed >= MAX_NEW_PLANTINGS:
			break
		if not stems_f1 or not plants_per_bed:
			break

		wanted = math.ceil(deficit / (stems_f1 * plants_per_bed))
		# A planting smaller than the protocol's minimum is not a planting anyone
		# would make. Rounding up over-supplies this week, but the surplus lands in
		# this planting's own flush weeks and the greedy loop then proposes fewer
		# plantings overall, which is the point.
		beds = max(wanted, min_beds)
		# One planting cannot span two blocks, because a block is the unit that is
		# held exclusively.
		beds = min(beds, block_capacity) if block_capacity else beds
		plants = beds * plants_per_bed
		planting_date = monday - datetime.timedelta(weeks=first_offset)
		p_year, p_week = iso_year_week(planting_date)
		sticking_date = planting_date - datetime.timedelta(weeks=stick_weeks)
		s_year, s_week = iso_year_week(sticking_date)
		uproot = planting_date + datetime.timedelta(weeks=life_weeks)

		# A block if one is free, and the planting either way. Which block a planting
		# goes in is decided when blocks are assigned; the plan does not wait for it.
		block = calendar.place(beds, planting_date, uproot)
		if not block:
			not_placed += 1
			unmet += deficit

		# Fold every flush of this proposed planting into the grid.
		family = set()
		mine = defaultdict(int)
		for off, spp in offsets:
			hd = planting_date + datetime.timedelta(weeks=off)
			if hd > uproot:
				break
			hy, hw = iso_year_week(hd)
			family.add(hw)
			if (hy, hw) in index:
				stems = int(round(spp * plants))
				production[(hy, hw)] += stems
				mine[(hy, hw)] += stems
				if block:
					placeable[(hy, hw)] += stems
				contributors[(hy, hw)].append(
					f"new {p_year}-W{p_week:02d} ({beds}b)"
					+ ("" if block else " — no block yet"))

		net_ha = (beds * (protocol.sqm_net_per_bed or 0)) / 10_000
		footprints.append((planting_date, uproot, net_ha))

		note = None
		plan.append("plan_blocks", {
			"is_new_planting": 1,
			"block": block or None,
			"beds": beds,
			"plants": plants,
			"sticking_year": s_year,
			"sticking_week": s_week,
			"planting_year": p_year,
			"planting_week": p_week,
			"planting_date": planting_date,
			"pinch_date": planting_date + datetime.timedelta(
				weeks=protocol.weeks_to_pinch or 0
			),
			"first_harvest_year": year,
			"first_harvest_week": week,
			"harvest_week_family": ", ".join(f"wk{w}" for w in sorted(family)),
			"net_area_ha": net_ha,
			"lifetime_stems": int(round(
				(protocol.total_stems_per_plant_life or 0) * plants
			)),
			"below_minimum": 1 if beds < min_beds else 0,
			"not_placed": 0 if block else 1,
			"planting_in_past": 1 if planting_date < today else 0,
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
		proposed += 1

	# ---- weekly grid
	plan.plantings_not_placed = not_placed
	plan.unmet_stems = unmet
	plan.blocks_used = calendar.used()

	# Beds required over a three-year horizon is a lifetime total: a block that
	# hosts two successive plantings contributes its beds twice, so comparing that
	# sum with the beds the farm has is meaningless. What the farm has to find room
	# for is the most beds standing at any one moment.
	occupied = []
	for row in plan.plan_blocks:
		if not row.get("is_new_planting") or row.get("not_placed"):
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
	# built in the same order and in the same pass, so row i of one is row i of the
	# other; anything else would be a second source of truth for the same stems.
	plan.matrix_json = frappe.as_json({
		"grid": [[y, w, str(m)] for (y, w, m) in grid],
		"rows": [{"weeks": {"%s-%s" % k: v for k, v in r["weeks"].items()},
		          "uproot": r["uproot"]}
		         for r in per_row],
	})

	plan.plan_weeks = []
	running = 0
	for (year, week, monday) in grid:
		prod = production[(year, week)]
		dem = demand_map.get((year, week), 0)
		running += prod - dem
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


class BlockCalendar:
	"""Which beds are free, in which block, and when.

	A planting reserves the beds it needs, not the whole block. Karen's blocks run
	from 20 to 120 beds and a planting is 10 to 34, so holding a whole block for one
	planting left seventy beds of a hundred idle for two years and reported that forty
	plantings needed forty blocks. Several plantings share a block where the beds and
	the dates both allow it, which is already how the farm works: Block 5A holds two.

	Occupancy is a list of (start, end, beds) reservations per block. Beds free in a
	window is the block's total less the most beds reserved at any moment inside it --
	the peak, not the sum, because two reservations that do not overlap in time do not
	compete for the same beds.
	"""

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

	def _seed_standing(self, variety):
		"""Whatever already claims a block holds it until it comes out.

		RESERVING_STATES, not STANDING_STATES: a Draft planting has not gone into
		the ground yet but it has claimed the block, and Planting Calendar refuses
		a second planting there on exactly that basis. Seeding only the approved
		ones let the planner propose blocks that create_plantings would reject.
		"""
		f = {"calendar_status": ["in", RESERVING_STATES]}
		for r in frappe.get_all(
				"Planting Calendar", filters=f,
				fields=["name", "block", "beds", "planting_date",
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
				"status": r.calendar_status,
				# Whether the crop is in the ground decides what freeing the beds
				# costs: a Draft reservation can simply be moved, a Planted one has
				# to be uprooted early and loses its remaining flushes.
				"in_ground": r.calendar_status in ("Planted",),
			})

	def _get(self, name):
		for b in self.blocks:
			if b["name"] == name:
				return b
		return None

	@staticmethod
	def _peak_reserved(block, start, end):
		"""The most beds reserved at any single moment inside a window.

		The sum would double count reservations that follow one another: two 40-bed
		plantings a year apart in an 80-bed block use 40 beds, not 80.
		"""
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
		"""The block that fits with the least room to spare.

		Tightest fit rather than largest-first: a 34-bed planting put into a 120-bed
		block leaves 86 beds fragmented across that planting's whole life, and the
		point of allocating by bed is to keep the big blocks whole for the plantings
		that need them.
		"""
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

	def candidates(self, beds, start, end, limit=6):
		"""Every block, ranked: the ones that fit, then the ones that nearly do.

		A block that frees up two weeks after the planting is wanted is worth seeing --
		moving the planting a fortnight is usually cheaper than finding land -- and so
		is knowing which planting holds it and what uprooting that one early would
		cost. Hiding occupied blocks would hide both.
		"""
		out = []
		for b in self.blocks:
			free = self.free_beds(b, start, end)
			if free >= beds:
				out.append({"block": b["name"], "total_beds": b["beds"],
				            "free_beds": free, "fits": True, "free_from": None,
				            "weeks_late": 0, "blockers": []})
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
			})
		out.sort(key=lambda c: (not c["fits"],
		                        c["free_beds"] - beds if c["fits"] else 0,
		                        c["weeks_late"] if c["weeks_late"] is not None else 9999))
		return out[:limit]

	def capacity(self):
		return max((b["beds"] for b in self.blocks), default=0)

	def block_count(self):
		"""How many blocks exist at all, before anything is booked.

		Zero is a different problem from all-of-them-busy and needs a different
		message: no amount of rescheduling frees a block the farm does not have.
		"""
		return len(self.blocks)

	def used(self):
		return len([b for b in self.blocks if b["busy"]])

	def get(self, name):
		return self._get(name)
