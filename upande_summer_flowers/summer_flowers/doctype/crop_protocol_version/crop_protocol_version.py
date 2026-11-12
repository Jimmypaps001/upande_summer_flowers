# Copyright (c) 2026, James Kiruga and contributors
"""A versioned, per-farm parameter set hanging off a variety's Crop Protocol."""

import math

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt, getdate, now_datetime, nowdate
from upande_summer_flowers.summer_flowers import stages as _stages

WEEKS_PER_YEAR = 52

# Net bed in a hectare of ground. A hectare is 10,000 m2, but paths, headlands and
BED_SQM_PER_HA = 6667


def bed_sqm_per_ha():
	from upande_summer_flowers.summer_flowers.settings import bed_sqm_per_ha as _b

	return _b(BED_SQM_PER_HA)


# What a stage is assumed to lose when the protocol does not say. Five per
DEFAULT_STAGE_LOSS_PCT = 5.0


class CropProtocolVersion(Document):
	def guard_snapshot_only(self):
		"""A version is written by approving a Crop Protocol, and by nothing else."""
		if self.flags.from_protocol_snapshot or self.flags.ignore_protocol_lock:
			return
		if (frappe.flags.in_migrate or frappe.flags.in_patch or frappe.flags.in_install
				or frappe.flags.in_test):
			return
		# A Draft is not history yet. Nothing is pinned to it -- no plan, no planting,
		if (self.version_status or "Draft") in ("Draft", "Pending Approval"):
			return
		frappe.throw(
			_("{0} is a snapshot and cannot be edited. Change the protocol on Crop "
			  "Protocol {1} and approve it — that writes a new version and leaves "
			  "this one as the record of what came before.").format(
				self.name, self.crop_protocol or ""),
			title=_("Read-only history"))

	def validate(self):
		self.guard_snapshot_only()
		self.set_from_route()
		self.set_variety()
		self.set_geometry()
		self.set_flush_schedule()
		self.set_cycle_totals()
		self.set_motherstock()
		self.set_grades_and_losses()
		self.check_effective_from()
		self.sync_status()
		self.set_title()

	# A figure the planner divides by, multiplies out or dates from. Missing
	# means zero, and zero does not stop a plan being drawn -- it draws a wrong
	# one and says nothing: every planting one bed, the land figures
	# meaningless, nothing to order, the block never free.
	#
	# NOTHING ENFORCES THIS. Approval demanded these figures for two days and
	# the farm asked for the demand removed: it is their protocol to approve,
	# and a blocked approval stops more work than a wrong figure does. The list
	# is kept because it is the written-down answer to "what does the planner
	# actually need", which is worth having even with no teeth.
	# (field, label, why it is needed.)
	PLANNING_INPUTS = (
		("plants_per_sqm_net", "Plants per m2 of bed (net)",
		 "how many plants a bed holds"),
		("sqm_net_per_bed", "Net area per bed (m2)",
		 "how many beds a planting takes, and how much land it is"),
		("plants_per_bed", "Plants per bed", "the size of every planting"),
		("min_planting_area_sqm", "Minimum planting area (m2)",
		 "the smallest planting worth putting in"),
		("total_weeks_in_ground", "Total weeks in ground",
		 "when the crop comes out and the block is free again"),
		("rooting_success_pct", "Rooting success %",
		 "how many cuttings to buy for the plants wanted"),
		("field_establishment_pct", "Field establishment %",
		 "how many plants survive to stand in the field"),
		("cuttings_per_plant_required", "Cuttings per plant required",
		 "the order quantity sent to the supplier"),
	)

	def set_title(self):
		"""What this snapshot is called, as against what it is named."""
		from upande_summer_flowers.summer_flowers.crop_protocol import variety_name

		parts = [variety_name(self.variety), self.farm]
		self.title = " - ".join(x for x in parts if x)
		if cint(self.version):
			self.title = "{0} v{1}".format(self.title, cint(self.version))

	def on_update(self):
		# Frappe workflows only move workflow_state; they do not call into the
		before = self.get_doc_before_save()
		became_active = self.workflow_state == "Active" and (
			not before or before.workflow_state != "Active"
		)
		if became_active:
			self.on_activate()
		else:
			self.refresh_current_flag()

	# ------------------------------------------------------------------ identity
	def set_variety(self):
		if self.crop_protocol:
			self.variety = frappe.db.get_value("Crop Protocol", self.crop_protocol, "variety")

	def check_effective_from(self):
		"""A version cannot start before the one it supersedes."""
		if not (self.supersedes and self.effective_from):
			return
		prior = frappe.db.get_value(
			"Crop Protocol Version", self.supersedes, "effective_from"
		)
		if prior and getdate(self.effective_from) <= getdate(prior):
			frappe.throw(
				_("Effective From must be after {0}, the date version {1} took effect.").format(
					frappe.format(prior, {"fieldtype": "Date"}), self.supersedes
				)
			)

	def sync_status(self):
		# Superseded is terminal and is set by the successor's activation, not by the
		if self.version_status == "Superseded":
			return
		if self.workflow_state and self.workflow_state in (
			"Draft", "Pending Approval", "Active"
		):
			self.version_status = self.workflow_state

	# ------------------------------------------------------------------ geometry
	def set_geometry(self):
		"""Everything geometric comes off net bed area."""
		if not self.plants_per_sqm_net:
			frappe.throw(_("Plants per m² of bed (net) is required."))

		# Two inputs decide the geometry: how densely the crop is planted, and how big
		bed_sqm = flt(self.sqm_net_per_bed) or flt(self.min_planting_area_sqm)
		self.bed_area_assumed = 1 if (not self.sqm_net_per_bed and bed_sqm) else 0

		self.plants_per_bed = int(round(bed_sqm * flt(self.plants_per_sqm_net))) \
			if bed_sqm else 0

		# The minimum planting is an area. Beds are whole, so it rounds up to one.
		beds = 0
		if self.min_planting_area_sqm and bed_sqm:
			beds = int(math.ceil(flt(self.min_planting_area_sqm) / bed_sqm))
		self.min_planting_beds_derived = (max(beds, 1)
		                                  if (self.min_planting_area_sqm and bed_sqm)
		                                  else 0)
		self.min_planting_plants = (self.min_planting_beds_derived or 0) \
			* (self.plants_per_bed or 0)

	# --------------------------------------------------------------------- flush
	def set_flush_schedule(self):
		"""Number the flushes, date them, and reconcile stems against percentages."""
		rows = sorted(self.flush_schedule, key=lambda r: r.flush_number or 0)
		interval = self.flush_interval_weeks or 0

		stems_given = sum(flt(r.stems_per_plant) for r in rows)
		pct_given = sum(flt(r.pct_of_life) for r in rows)
		stated = flt(self.stated_stems_per_plant_life)
		# What a plant gives over its life is the sum of its flushes -- there is
		if stems_given:
			self.stated_stems_per_plant_life = stems_given
		if not stems_given and pct_given and stated:
			# Shares of a stated life. Each flush's stems are its share of it.
			for r in rows:
				r.stems_per_plant = stated * flt(r.pct_of_life) / 100.0
			stems_given = sum(flt(r.stems_per_plant) for r in rows)

		cumulative = 0.0
		for idx, row in enumerate(rows, start=1):
			row.idx = idx
			row.flush_number = idx
			# Planting is what is typed; pinch is worked back from it. Every crop has a
			pinch = cint(self.weeks_to_pinch)
			if cint(row.weeks_from_planting):
				row.weeks_from_pinch = cint(row.weeks_from_planting) - pinch
			elif not row.weeks_from_pinch and interval:
				row.weeks_from_pinch = interval * idx
			row.weeks_from_planting = pinch + cint(row.weeks_from_pinch)
			row.harvest_week_of_year = "+{0}".format(row.weeks_from_planting)
			if stems_given:
				row.pct_of_life = flt(row.stems_per_plant) / stems_given * 100.0
			cumulative += flt(row.pct_of_life)
			row.cumulative_pct = cumulative

		# The interval is a seed for filling blank offsets, and once the rows carry
		gaps = sorted({cint(rows[i + 1].weeks_from_pinch) - cint(rows[i].weeks_from_pinch)
		               for i in range(len(rows) - 1)})
		if gaps and gaps[0] > 0:
			self.flush_interval_weeks = gaps[0]

		self.flush_schedule = rows

	CONTINUOUS = "Perennial - continuous"

	# ---------------------------------------------------------------- the route
	def route_blocks(self):
		"""The route as [(stage, [its steps])]. A step belongs to the stage above it."""
		blocks, current = [], None
		for row in (self.material_route or []):
			if (row.row_type or "Stage") == "Step":
				if current:
					current[1].append(row)
			else:
				current = (row, [])
				blocks.append(current)
		return blocks

	def set_from_route(self):
		"""Take the per-stage figures off the route, where the route states them."""
		blocks = self.route_blocks()
		steps = {s.step: s for _stage, rows in blocks for s in rows if s.step}
		if not steps:
			return

		for step, field in (("Tray", "weeks_on_tray"),
		                    ("Pot", "weeks_on_pot"),
		                    ("Hardening", "hardening_weeks"),
		                    ("Sticking", "sticking_to_planting_weeks"),
		                    ("Bulking", "ramp_weeks")):
			if step in steps:
				setattr(self, field, cint(steps[step].weeks))

		for step, field in (("Sticking", "rooting_success_pct"),
		                    ("Field establishment", "field_establishment_pct")):
			row = steps.get(step)
			if row and flt(row.loss_pct):
				setattr(self, field, round(100.0 - flt(row.loss_pct), 4))

		bought = next((st for st, _ in blocks if cint(st.is_purchase)), None)
		if bought:
			if cint(bought.lead_weeks):
				self.supplier_lead_weeks = cint(bought.lead_weeks)
			if flt(bought.loss_pct):
				self.tc_order_loss_pct = flt(bought.loss_pct)

		# Cycles a stage may be run round again: motherstock generations, or the
		mult = next((st for st, _ in blocks
		             if st.stage in _stages.StageSet(flag="multiplies")), None)
		if mult and cint(mult.max_cycles):
			self.max_multiplication_cycles = cint(mult.max_cycles)

	def harvest_window_weeks(self):
		"""How many weeks of the year the crop is allowed to cut, if it is limited."""
		a, b = cint(self.harvest_window_start_week), cint(self.harvest_window_end_week)
		if not a or not b:
			return 0
		return (b - a + 1) if b >= a else (WEEKS_PER_YEAR - a + 1 + b)

	def set_continuous_totals(self):
		"""The totals for a crop that cuts every week instead of in flushes."""
		first = cint(self.weeks_planting_to_first_cut)
		productive = cint(self.productive_weeks)
		per_week = flt(self.stems_per_plant_per_week)

		self.total_flushes = 0
		self.flushes_per_year = 0
		self.first_harvest_offset_weeks = first
		self.total_weeks_in_ground = first + productive
		self.total_stems_per_plant_life = per_week * productive
		self.harvest_weeks_per_year = (
			self.harvest_window_weeks() or min(productive, WEEKS_PER_YEAR))

	def set_cycle_totals(self):
		rows = self.flush_schedule
		if self.growing_cycle == self.CONTINUOUS and not rows:
			self.set_continuous_totals()
			years = (self.total_weeks_in_ground or 0) / WEEKS_PER_YEAR
			self.life_expectancy_years = years
			self.set_per_hectare(years)
			return
		self.total_flushes = len(rows)
		self.total_stems_per_plant_life = sum((r.stems_per_plant or 0) for r in rows)

		last = max((r.weeks_from_pinch or 0) for r in rows) if rows else 0
		self.total_weeks_in_ground = (self.weeks_to_pinch or 0) + last

		first = min((r.weeks_from_pinch or 0) for r in rows) if rows else 0
		# Pinch plus the first flush, and nothing else. A rounding allowance used to
		self.first_harvest_offset_weeks = (self.weeks_to_pinch or 0) + first

		# How many harvests land in a year: counted off the schedule rather than
		offs = sorted(cint(r.weeks_from_pinch) for r in rows)
		gaps = {offs[i + 1] - offs[i] for i in range(len(offs) - 1)}
		best = 0
		for i, a in enumerate(offs):
			best = max(best, sum(1 for b in offs[i:] if b - a < WEEKS_PER_YEAR))
		if len(gaps) <= 1 and self.flush_interval_weeks:
			# Evenly spaced: the interval divides the year, and that is the figure
			self.harvest_weeks_per_year = min(
				round(WEEKS_PER_YEAR / self.flush_interval_weeks), best or 0)
		else:
			self.harvest_weeks_per_year = best

		years = (self.total_weeks_in_ground or 0) / WEEKS_PER_YEAR
		self.flushes_per_year = (self.total_flushes / years) if years else 0

		self.life_expectancy_years = years
		self.set_per_hectare(years)

	def set_per_hectare(self, years):
		# Per hectare means per hectare of GROUND, of which bed_sqm_per_ha() is bed.
		plants_per_ha = (self.plants_per_sqm_net or 0) * bed_sqm_per_ha()
		self.plants_per_net_ha = int(round(plants_per_ha))
		self.stems_per_ha_life = (self.total_stems_per_plant_life or 0) * plants_per_ha
		self.stems_per_ha_year = (self.stems_per_ha_life / years) if years else 0

		# The best year, not the average one. Flushes are not evenly spaced across a
		self.best_year_stems_per_plant = self.best_year_per_plant()
		self.best_year_stems_per_net_ha = self.best_year_stems_per_plant * plants_per_ha

		# A target yield and a computed one rarely agree; show the gap instead of
		if self.stated_yield_stems_per_ha and self.stems_per_ha_year:
			self.yield_variance_pct = (
				(self.stems_per_ha_year - self.stated_yield_stems_per_ha)
				/ self.stated_yield_stems_per_ha * 100
			)
		else:
			self.yield_variance_pct = 0

		self.plants_per_block = (self.beds_per_block or 0) * (self.plants_per_bed or 0)

	# --------------------------------------------------------------- motherstock
	def set_motherstock(self):
		self.establishment_weeks = (
			(self.weeks_on_tray or 0) + (self.weeks_on_pot or 0)
			+ (self.weeks_to_max_pc or 0) + (self.hardening_weeks or 0)
		)
		# What a cutting actually needs to become a productive mother: tray, pot,
		self.ms_establishment_weeks = (
			(self.weeks_on_tray or 0) + (self.weeks_on_pot or 0) + (self.ramp_weeks or 0)
			+ (cint(self.hardening_weeks) if self.establishment_includes_hardening else 0)
		)
		self.weeks_tc_to_first_cut_derived = self.weeks_tc_to_first_cut()
		self.cutting_to_harvest_weeks = (
			(self.hardening_weeks or 0) + (self.weeks_to_pinch or 0)
			+ (self.flush_interval_weeks or 0)
		)
		self.plants_per_sqm_bench = (self.pots_per_sqm or 0) * (self.plants_per_pot or 0)

		cycles = self.max_multiplication_cycles or 0
		self.max_multiplication_factor = self.multiplication_factor(cycles)
		self.lead_time_weeks = self.lead_time_for_cycles(cycles)

	def ramp_ratios(self):
		"""The climb to full cutting capacity, as one ratio per week."""
		from upande_summer_flowers.summer_flowers.lifecycle_sim import parse_ramp

		return parse_ramp(self.ramp_profile,
		                  cint(self.ramp_weeks) or cint(self.weeks_to_max_pc))

	def best_year_per_plant(self):
		"""Most stems one plant gives in any 52 consecutive weeks of its life."""
		if self.growing_cycle == self.CONTINUOUS and not self.flush_schedule:
			# Every producing week is the same week, so the best year is simply as
			weeks = min(cint(self.productive_weeks), WEEKS_PER_YEAR)
			window = self.harvest_window_weeks()
			return flt(self.stems_per_plant_per_week) * min(weeks, window or weeks)
		rows = sorted(self.flush_schedule, key=lambda r: r.weeks_from_pinch or 0)
		best = 0.0
		for i, start in enumerate(rows):
			window = 0.0
			for r in rows[i:]:
				if (r.weeks_from_pinch or 0) - (start.weeks_from_pinch or 0) >= WEEKS_PER_YEAR:
					break
				window += flt(r.stems_per_plant)
			best = max(best, window)
		return best

	def weeks_tc_to_first_cut(self):
		"""Weeks from a TC plantlet arriving to the first cutting off it."""
		weeks = (self.weeks_on_tray or 0) + (self.weeks_on_pot or 0)
		if self.establishment_includes_ramp:
			weeks += cint(self.ramp_weeks) or cint(self.weeks_to_max_pc)
		if self.establishment_includes_hardening:
			weeks += cint(self.hardening_weeks)
		return weeks

	def lead_time_for_cycles(self, cycles):
		"""Weeks from receiving TC plantlets to a pool of the full size."""
		est = self.weeks_tc_to_first_cut()
		return est * max(1, cint(cycles))

	def multiplication_factor(self, cycles=None):
		"""How many mother plants one TC plantlet ends up as."""
		from upande_summer_flowers.summer_flowers.crop_protocol import (
			multiplication_factor,
		)

		if cycles is None:
			cycles = self.max_multiplication_cycles or 0
		return multiplication_factor(cycles, self.multiplication_factor_per_cycle)

	def mother_plants_for(self, tc_plants, cycles=None):
		"""Mother plants a given order of plantlets ends up as. The inverse of"""
		factor = self.multiplication_factor(cycles)
		loss = flt(self.tc_order_loss_pct) / 100
		arriving = flt(tc_plants) * (1 - loss) if 0 < loss < 1 else flt(tc_plants)
		return arriving * factor

	def tc_plants_for(self, mother_plants, cycles=None, with_loss=True):
		"""Plantlets to order for a target number of mother plants."""
		factor = self.multiplication_factor(cycles)
		needed = (mother_plants / factor) if factor else mother_plants
		if not with_loss:
			return needed
		loss = flt(self.tc_order_loss_pct) / 100
		return needed / (1 - loss) if 0 < loss < 1 else needed

	# ---------------------------------------------------------- grades & losses
	def set_grades_and_losses(self):
		self.grade_total_pct = sum((r.allocation_pct or 0) for r in self.grade_allocation)
		if self.grade_allocation and abs(self.grade_total_pct - 100) > 0.01:
			frappe.msgprint(
				_("Grade allocation totals {0}%, not 100%.").format(self.grade_total_pct),
				indicator="orange", title=_("Check grade split"),
			)

		# A stage nobody has measured still loses plants, so a blank takes the
		# house allowance. A figure somebody typed is an answer and is kept --
		# 100 included. Treating 100 as "nobody filled this in" meant a farm
		# that said a stage loses nothing had 95% written back over it on every
		# save, through the derivation that copies the version's read-only
		# fields onto the protocol.
		if not flt(self.rooting_success_pct):
			self.rooting_success_pct = 100.0 - DEFAULT_STAGE_LOSS_PCT
		if not flt(self.field_establishment_pct):
			self.field_establishment_pct = 100.0 - DEFAULT_STAGE_LOSS_PCT
		if not flt(self.cutting_reject_pct):
			self.cutting_reject_pct = DEFAULT_STAGE_LOSS_PCT

		rooting = (self.rooting_success_pct or 100) / 100
		field = (self.field_establishment_pct or 100) / 100
		survival = rooting * field
		self.cuttings_per_plant_required = (1 / survival) if survival else 1

	# ------------------------------------------------------------------ helpers
	SINGLE = "Single flush cycle"

	def flush_offsets(self):
		"""[(weeks after planting the harvest lands, stems per plant), ...]"""
		rows = sorted(self.flush_schedule, key=lambda r: r.flush_number or 0)
		if rows:
			return [
				(
					(self.weeks_to_pinch or 0) + (r.weeks_from_pinch or 0),
					r.stems_per_plant or 0,
				)
				for r in rows
			]

		first = cint(self.first_harvest_offset_weeks)
		if not first:
			return []

		if self.growing_cycle == self.CONTINUOUS:
			per_week = flt(self.stems_per_plant_per_week)
			weeks = cint(self.productive_weeks)
			if not per_week or not weeks:
				return []
			return [(first + i, per_week) for i in range(weeks)]

		if self.growing_cycle == self.SINGLE:
			# Cut once and the block comes out. The whole life yield lands in one week,
			return [(first, flt(self.total_stems_per_plant_life))]

		return []

	def stems_per_plant_at(self, weeks_after_planting):
		for offset, stems in self.flush_offsets():
			if offset == weeks_after_planting:
				return stems
		return 0

	def beds_for_stems(self, stems, flush_index=1):
		offsets = self.flush_offsets()
		if not offsets or flush_index > len(offsets):
			return 0
		stems_per_plant = offsets[flush_index - 1][1]
		if not stems_per_plant or not self.plants_per_bed:
			return 0
		return math.ceil((stems / stems_per_plant) / self.plants_per_bed)

	def harvest_week_family(self, planting_week):
		"""The weeks of the year a planting will harvest in, for its whole life."""
		interval = self.flush_interval_weeks or 0
		if not interval:
			return []
		first = planting_week + (self.first_harvest_offset_weeks or 0)
		count = self.harvest_weeks_per_year or 0
		return sorted({((first + interval * k - 1) % WEEKS_PER_YEAR) + 1 for k in range(count)})

	def cuttings_for_plants(self, plants):
		"""Cuttings to stick for a target number of standing plants."""
		return int(math.ceil(plants * (self.cuttings_per_plant_required or 1)))

	# ------------------------------------------------------------- versioning
	def refresh_current_flag(self):
		"""Exactly one Active version per (protocol, farm) is current."""
		if self.version_status != "Active":
			if self.is_current:
				self.db_set("is_current", 0, update_modified=False)
			return

		siblings = frappe.get_all(
			"Crop Protocol Version",
			filters={
				"crop_protocol": self.crop_protocol,
				"farm": self.farm,
				"version_status": "Active",
				"name": ["!=", self.name],
			},
			fields=["name", "effective_from"],
		)
		latest = max(
			[getdate(s.effective_from) for s in siblings] + [getdate(self.effective_from)]
		)
		self.db_set(
			"is_current", 1 if getdate(self.effective_from) == latest else 0,
			update_modified=False,
		)

	def on_activate(self):
		"""Called when the approval workflow moves this version to Active."""
		self.db_set("approved_by", frappe.session.user, update_modified=False)
		self.db_set("approved_on", now_datetime(), update_modified=False)
		self.db_set("version_status", "Active", update_modified=False)

		if self.supersedes:
			prior = frappe.get_doc("Crop Protocol Version", self.supersedes)
			prior.db_set("superseded_by", self.name, update_modified=False)
			prior.db_set("version_status", "Superseded", update_modified=False)
			prior.db_set("is_current", 0, update_modified=False)
			# The previous version's window closes the day before this one opens, so
			prior.db_set(
				"effective_to", add_days(getdate(self.effective_from), -1),
				update_modified=False,
			)

		self.refresh_current_flag()

	@frappe.whitelist()
	def create_amendment(self, change_reason=None, effective_from=None):
		"""Copy this version into a new Draft for amendment."""
		if self.version_status != "Active":
			frappe.throw(_("Only an Active version can be amended."))
		if not change_reason:
			frappe.throw(_("A change reason is required to amend a protocol version."))

		new = frappe.copy_doc(self)
		new.version = (self.version or 0) + 1
		new.version_status = "Draft"
		new.workflow_state = "Draft"
		new.supersedes = self.name
		new.superseded_by = None
		new.effective_to = None
		new.is_current = 0
		new.approved_by = None
		new.approved_on = None
		new.change_reason = change_reason
		new.effective_from = effective_from or add_days(getdate(nowdate()), 1)
		new.insert()
		return new.name


# ---------------------------------------------------------------------------

@frappe.whitelist()
def current_version(variety, farm):
	"""The Active version a plan for this crop at this farm should start from."""
	rows = frappe.get_all(
		"Crop Protocol Version",
		filters={"variety": variety, "farm": farm, "version_status": "Active"},
		fields=["name"],
		order_by="is_current desc, effective_from desc, version desc",
		limit=1,
	)
	return rows[0].name if rows else None


@frappe.whitelist()
def resolve_version(crop_protocol, farm, on_date=None):
	"""The version in force for a variety at a farm on a given date."""
	on_date = getdate(on_date or nowdate())
	rows = frappe.get_all(
		"Crop Protocol Version",
		filters={
			"crop_protocol": crop_protocol,
			"farm": farm,
			"version_status": ["in", ["Active", "Superseded"]],
			"effective_from": ["<=", on_date],
		},
		fields=["name", "version", "effective_from", "effective_to"],
		order_by="effective_from desc, version desc",
	)
	for r in rows:
		if not r.effective_to or getdate(r.effective_to) >= on_date:
			return r.name
	return rows[0].name if rows else None
