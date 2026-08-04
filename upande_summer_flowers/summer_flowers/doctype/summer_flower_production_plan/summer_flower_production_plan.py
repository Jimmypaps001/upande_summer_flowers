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
		"""Approval is the workflow transition that submits the plan."""
		self.db_set("status", "Approved")
		self.create_budget()

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

	def set_totals(self):
		weeks = self.plan_weeks
		self.total_production_stems = sum((w.production_stems or 0) for w in weeks)
		self.planned_production_stems = sum(
			(w.get("planned_production_stems") or 0) for w in weeks)
		self.total_demand_stems = sum((w.demand_stems or 0) for w in weeks)
		self.total_variance_stems = self.total_production_stems - self.total_demand_stems
		self.coverage_pct = (
			self.total_production_stems / self.total_demand_stems * 100
			if self.total_demand_stems else 0
		)
		self.planned_coverage_pct = (
			self.planned_production_stems / self.total_demand_stems * 100
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

		# Only plantings that have a block count towards what has to be resourced.
		# A proposal with nowhere to go is a statement of what the farm cannot do,
		# not a bed to prepare or a cutting to stick, and counting it would ask
		# propagation to raise plants that will never be planted.
		new_rows = [b for b in self.plan_blocks
		            if b.is_new_planting and not b.get("not_placed")]
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

		# The planned peak includes plantings with no block. That is the number the
		# TC order is sized from: the order has to be placed months before anyone
		# knows which block will be free, and ordering for the placed subset would
		# guarantee the plan can never be met even after the blocks are sorted out.
		planned_sticking = defaultdict(int)
		for b in self.plan_blocks:
			if not b.is_new_planting:
				continue
			if b.sticking_year and b.sticking_week:
				planned_sticking[(b.sticking_year, b.sticking_week)] += b.plants or 0
		self.peak_weekly_sticking_planned = (
			max(planned_sticking.values()) if planned_sticking else 0)
		self.set_tc_order(planned_sticking)

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

		# Working back from the first sticking week. Two distinct waits, and only
		# two: the lab's own order-to-delivery, then lead_time_weeks, which is
		# already defined as arrival to first cutting including any multiplication
		# cycles. Adding ms_establishment_weeks on top would count tray and pot
		# twice, because lead_time_weeks contains them.
		from upande_summer_flowers.summer_flowers.doctype.summer_flower_motherstock_batch.summer_flower_motherstock_batch import (
			lab_lead_weeks,
		)

		first = min(planned_sticking) if planned_sticking else None
		if first:
			stick_monday = iso_monday(first[0], first[1])
			weeks_back = cint(lab_lead_weeks(v)) + cint(v.lead_time_weeks)
			self.tc_order_by_date = stick_monday - datetime.timedelta(weeks=weeks_back)

		notes = []
		if self.tc_order_by_date and self.tc_order_by_date < getdate(nowdate()):
			notes.append(_(
				"The order date has already passed ({0}), so the first sticking week "
				"{1}-W{2:02d} cannot be met from a new TC order. Buy rooted cuttings "
				"for the early weeks or move the demand later."
			).format(self.tc_order_by_date, first[0], first[1]))
		if cint(self.plantings_not_placed):
			notes.append(_(
				"{0} of the proposed plantings have no block. This order is sized for "
				"the whole plan, so buying it commits to finding room for them."
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

		return build_from_plan(self.name)

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
	def regenerate(self):
		"""Rebuild the weekly grid and planting proposals in place."""
		if self.docstatus != 0:
			frappe.throw(_("Only a draft plan can be regenerated."))
		_populate(self)
		self.save()
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
	contributors = defaultdict(list)
	# (start_date, end_date, gross_area_ha) for the area-standing curve
	footprints = []

	# ---- what is already on the ground
	plan.plan_blocks = []
	for planting in standing_plantings(plan.farm, plan.variety):
		hit = False
		for (y, w), stems in production_by_week(planting).items():
			if (y, w) in index:
				production[(y, w)] += stems
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
	# What the plan would deliver if every proposal had somewhere to go. Keeping only
	# the placeable figure meant a plan whose blocks were all taken read zero
	# production and looked identical to a plan that grows nothing, with the reason
	# buried in thirteen row notes.
	#
	# This is the deficit each unplaceable proposal was meant to close, not that
	# proposal's full yield. The loop retries a week it could not place, so summing
	# the retries counted the same shortfall thirteen times over and reported 599%
	# planned coverage against a demand of 114,000.
	unmet_by_week = defaultdict(int)

	proposed = 0
	for (year, week, monday) in grid:
		# Against what can actually be grown. Measuring against the proposals instead
		# -- counting an unplaceable one as covering its week -- stopped the loop
		# retrying that week, and the retries are how later proposals find a block
		# that has come free: Aster at Karen fell from 70.7% coverage to 60.0%.
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

		# Find a block free for the planting's whole life before counting any of
		# its stems. A planting with nowhere to go does not happen, so folding its
		# flushes into the grid would report production the farm cannot grow.
		block = calendar.place(beds, planting_date, uproot)
		if not block:
			not_placed += 1
			unmet += deficit
			unmet_by_week[(year, week)] += deficit
			plan.append("plan_blocks", {
				"is_new_planting": 1,
				"block": None,
				"beds": beds,
				"plants": plants,
				"sticking_year": s_year,
				"sticking_week": s_week,
				"planting_year": p_year,
				"planting_week": p_week,
				"planting_date": planting_date,
				"net_area_ha": (beds * (protocol.sqm_net_per_bed or 0)) / 10_000,
				"below_minimum": 0,
				"not_placed": 1,
				"planting_in_past": 1 if planting_date < today else 0,
				"notes": (
					_("{0} has no summer flower blocks, so nothing can be planted "
					  "there. Mark its blocks as summer flower blocks, or plan this "
					  "variety at a farm that has them.").format(plan.farm)
					if not calendar.block_count() else
					_("Every block big enough for {0} beds is already held by another "
					  "planting for some part of {1} to {2}. Not counted as "
					  "production.").format(beds, planting_date, uproot)
				),
			})
			proposed += 1
			continue

		# Fold every flush of this proposed planting into the grid.
		family = set()
		for off, spp in offsets:
			hd = planting_date + datetime.timedelta(weeks=off)
			if hd > uproot:
				break
			hy, hw = iso_year_week(hd)
			family.add(hw)
			if (hy, hw) in index:
				production[(hy, hw)] += int(round(spp * plants))
				contributors[(hy, hw)].append(f"new {p_year}-W{p_week:02d} ({beds}b)")

		net_ha = (beds * (protocol.sqm_net_per_bed or 0)) / 10_000
		footprints.append((planting_date, uproot, net_ha))

		note = None
		plan.append("plan_blocks", {
			"is_new_planting": 1,
			"block": block,
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
			"not_placed": 0,
			"planting_in_past": 1 if planting_date < today else 0,
			"notes": note,
		})
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
			"planned_production_stems": prod + unmet_by_week[(year, week)],
			"demand_stems": dem,
			"variance_stems": prod - dem,
			"cumulative_variance": running,
			"area_ha": sum(a for s, e, a in footprints if s <= monday <= e),
			"contributing_plantings": "\n".join(contributors[(year, week)]) or None,
		})


class BlockCalendar:
	"""Which block is free, and when.

	A block holds one planting at a time, but only for that planting's life --
	once it is uprooted the block is available again. The previous version popped
	a block out of the pool for good, so a three-year plan could never use more
	than one planting per block and everything after the thirteenth proposal came
	back unplaceable. Occupancy is a set of windows per block instead.
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
				fields=["block", "planting_date", "planned_uproot_date",
				        "actual_uproot_date"]):
			b = self._get(r.block)
			if not b or not r.planting_date:
				continue
			end = r.actual_uproot_date or r.planned_uproot_date
			b["busy"].append((getdate(r.planting_date),
			                  getdate(end) if end else getdate(r.planting_date)))

	def _get(self, name):
		for b in self.blocks:
			if b["name"] == name:
				return b
		return None

	def place(self, beds, start, end):
		"""Smallest block that fits and is free for the whole window.

		Smallest-that-fits rather than largest-first: taking a 40-bed block for an
		8-bed planting wastes the block for the planting's whole life, and blocks
		are the scarce thing here, not beds.
		"""
		best = None
		for b in self.blocks:
			if b["beds"] < beds:
				continue
			if any(not (end <= s or start >= e) for s, e in b["busy"]):
				continue
			if best is None or b["beds"] < best["beds"]:
				best = b
		if best is None:
			return None
		best["busy"].append((start, end))
		return best["name"]

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
