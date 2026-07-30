# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt

import datetime
import math
from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate

from upande_summer_flowers.summer_flowers.doctype.planting_calendar.planting_calendar import (
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
		self.set_period_end()
		self.roll_up_months()
		self.set_totals()
		self.sync_status()

	def on_submit(self):
		"""Approval is the workflow transition that submits the plan."""
		self.db_set("status", "Approved")
		self.create_budget()

	def on_cancel(self):
		self.db_set("status", "Rejected")

	# ------------------------------------------------------------------ header
	def pull_header_from_demand(self):
		if not self.market_demand:
			return
		d = frappe.get_cached_doc("Summer Flower Market Demand", self.market_demand)
		self.variety = d.variety
		self.farm = d.farm
		self.protocol = d.protocol
		# The demand register is authoritative. Do not fall back to whatever
		# frappe.new_doc pre-filled from the global default company -- on a
		# multi-company site that silently plans against the wrong entity.
		self.company = d.company or self.company
		self.currency = d.currency or self.currency
		if not self.price_per_stem:
			self.price_per_stem = d.price_per_stem

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
		self.total_demand_stems = sum((w.demand_stems or 0) for w in weeks)
		self.total_variance_stems = self.total_production_stems - self.total_demand_stems
		self.coverage_pct = (
			self.total_production_stems / self.total_demand_stems * 100
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
# Generation
# ---------------------------------------------------------------------------

@frappe.whitelist()
def build_from_demand(market_demand, from_year=None, from_week=None, weeks=None):
	"""Create a draft Production Plan covering a slice of a demand register."""
	demand = frappe.get_doc("Summer Flower Market Demand", market_demand)
	if not demand.demand_weeks:
		frappe.throw(_("Demand register {0} has no weeks.").format(market_demand))

	first = demand.demand_weeks[0]
	plan = frappe.new_doc("Summer Flower Production Plan")
	plan.market_demand = market_demand
	plan.from_year = frappe.utils.cint(from_year) or first.year
	plan.from_week = frappe.utils.cint(from_week) or first.week_no
	plan.weeks_covered = frappe.utils.cint(weeks) or min(len(demand.demand_weeks), 156)
	plan.pull_header_from_demand()

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
	protocol = frappe.get_cached_doc("Crop Protocol Version", plan.protocol)

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
			planting.gross_area_ha or 0,
		))
		if hit:
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
				"first_harvest_year": planting.first_harvest_year,
				"first_harvest_week": planting.first_harvest_week,
				"harvest_week_family": planting.harvest_week_family,
				"gross_area_ha": planting.gross_area_ha,
				"lifetime_stems": planting.lifetime_stems,
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
	free_beds = _free_bed_pool(plan.farm)

	proposed = 0
	for (year, week, monday) in grid:
		deficit = demand_map.get((year, week), 0) - production[(year, week)]
		if deficit <= 0:
			continue
		if proposed >= MAX_NEW_PLANTINGS:
			break
		if not stems_f1 or not plants_per_bed:
			break

		beds = math.ceil(deficit / (stems_f1 * plants_per_bed))
		plants = beds * plants_per_bed
		planting_date = monday - datetime.timedelta(weeks=first_offset)
		p_year, p_week = iso_year_week(planting_date)
		sticking_date = planting_date - datetime.timedelta(weeks=stick_weeks)
		s_year, s_week = iso_year_week(sticking_date)
		uproot = planting_date + datetime.timedelta(weeks=life_weeks)

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

		gross_ha = (beds * (protocol.sqm_gross_per_bed or 0)) / 10_000
		footprints.append((planting_date, uproot, gross_ha))

		block, note = _take_beds(free_beds, beds)
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
			"gross_area_ha": gross_ha,
			"lifetime_stems": int(round(
				(protocol.total_stems_per_plant_life or 0) * plants
			)),
			"below_minimum": 1 if beds < (protocol.min_planting_beds or 0) else 0,
			"planting_in_past": 1 if planting_date < today else 0,
			"notes": note,
		})
		proposed += 1

	# ---- weekly grid
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
			"demand_stems": dem,
			"variance_stems": prod - dem,
			"cumulative_variance": running,
			"area_ha": sum(a for s, e, a in footprints if s <= monday <= e),
			"contributing_plantings": "\n".join(contributors[(year, week)]) or None,
		})


def _free_bed_pool(farm):
	"""Summer flower blocks at this farm with spare beds, most spare first.

	A block holds one planting at a time, so a block already carrying a standing
	planting offers nothing regardless of how many beds are notionally free.
	"""
	rows = frappe.get_all(
		"Block",
		filters={"farm": farm, "custom_is_summer_flower_block": 1},
		fields=["name", "custom_total_beds", "custom_current_planting"],
		order_by="custom_total_beds desc",
	)
	return [
		[r.name, r.custom_total_beds or 0]
		for r in rows
		if not r.custom_current_planting
	]


def _take_beds(pool, beds):
	"""Allocate a whole block to this planting. Returns (block, note).

	Because a block holds one planting at a time, the block leaves the pool once
	taken rather than being topped up to capacity. A planting need not fill the
	block.
	"""
	for i, entry in enumerate(pool):
		if entry[1] >= beds:
			pool.pop(i)
			return entry[0], None
	return None, _(
		"No unoccupied block at this farm has {0} beds — allocate manually."
	).format(beds)
