# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""One planting on one block, approved by a Farm Manager before execution.

A block holds one planting at a time, so occupancy is checked as a date-range
overlap rather than by summing beds. The parameters come from the Crop Protocol
Version in force on the planting date, and stay pinned to it afterwards.
"""

import datetime

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, flt, getdate, now_datetime, nowdate

from upande_summer_flowers.summer_flowers.planning import iso_year_week

# What counts as standing on a block *today* for coverage reporting.
STANDING_STATES = ("Approved", "Planted")
# What reserves a block's date window. A draft still reserves it, otherwise two
# overlapping plantings can both be created and only collide later at approval.
RESERVING_STATES = ("Draft", "Pending Approval", "Approved", "Planted")


class PlantingCalendar(Document):
	@property
	def version(self):
		"""The protocol version this planting runs on.

		Resolved lazily rather than assigned in validate: whitelisted methods are
		called on a freshly loaded document, where validate has not run, and an
		instance attribute set there would not exist yet.
		"""
		if not self.crop_protocol_version:
			frappe.throw(_("{0} has no Crop Protocol Version.").format(
				self.name or _("This planting")))
		cached = self.get("_version_doc")
		if cached is None or cached.name != self.crop_protocol_version:
			cached = frappe.get_cached_doc("Crop Protocol Version",
			                               self.crop_protocol_version)
			self._version_doc = cached
		return cached

	def validate(self):
		self.check_version_applies()
		self.set_size()
		self.set_dates()
		self.check_block_capacity()
		self.check_block_not_double_booked()
		self.allocate_beds()
		self.build_flush_projection()
		self.set_yield()
		self.check_seedling_source()
		self.sync_status()

	def on_update(self):
		self.refresh_block_coverage()

	def on_trash(self):
		self.refresh_block_coverage()

	# ------------------------------------------------------------------ version
	def check_version_applies(self):
		v = self.version
		if v.variety != self.variety:
			frappe.throw(
				_("Protocol version {0} is for variety {1}, not {2}.").format(
					v.name, v.variety, self.variety
				)
			)
		block_farm = frappe.db.get_value("Block", self.block, "farm")
		if block_farm and v.farm != block_farm:
			frappe.throw(
				_("Protocol version {0} belongs to farm {1} but block {2} is at {3}. "
				  "Climate parameters differ by farm.").format(
					v.name, v.farm, self.block, block_farm
				)
			)
		if self.planting_date and v.effective_from:
			if getdate(self.planting_date) < getdate(v.effective_from):
				frappe.throw(
					_("Version {0} only takes effect on {1}, after this planting date.").format(
						v.name, frappe.format(v.effective_from, {"fieldtype": "Date"})
					)
				)

	@frappe.whitelist()
	def pull_version_for_date(self):
		"""Resolve the version in force on the planting date."""
		from upande_summer_flowers.summer_flowers.doctype.crop_protocol_version.crop_protocol_version import (
			resolve_version,
		)

		protocol = frappe.db.get_value("Crop Protocol", {"variety": self.variety}, "name")
		farm = self.farm or frappe.db.get_value("Block", self.block, "farm")
		if not (protocol and farm):
			return None
		name = resolve_version(protocol, farm, self.planting_date)
		if name:
			self.crop_protocol_version = name
		return name

	# --------------------------------------------------------------------- size
	def set_size(self):
		v = self.version
		self.plants = (self.beds or 0) * (v.plants_per_bed or 0)
		self.net_area_sqm = (self.beds or 0) * (v.sqm_net_per_bed or 0)
		self.gross_area_ha = ((self.beds or 0) * (v.sqm_gross_per_bed or 0)) / 10_000

	def set_dates(self):
		v = self.version
		planted = getdate(self.planting_date)
		self.planting_year, self.planting_week = iso_year_week(planted)
		self.sticking_date = add_days(planted, -7 * (v.sticking_to_planting_weeks or 0))
		self.pinch_date = add_days(planted, 7 * (v.weeks_to_pinch or 0))
		self.planned_uproot_date = add_days(planted, 7 * (v.total_weeks_in_ground or 0))

	def end_date(self):
		return getdate(self.actual_uproot_date or self.planned_uproot_date)

	def first_harvest(self):
		"""(year, week) of flush 1, or (None, None) before the projection is built.

		There is no stored first_harvest_year/week field: the first flush is simply
		the head of flush_projection, so reading it from there keeps the two from
		drifting when the protocol version or planting date changes.
		"""
		for r in sorted(self.flush_projection, key=lambda r: r.flush_number or 0):
			return r.year, r.week_no
		return None, None

	# ---------------------------------------------------------------- occupancy
	def check_block_capacity(self):
		total = frappe.db.get_value("Block", self.block, "custom_total_beds") or 0
		if total and (self.beds or 0) > total:
			frappe.throw(
				_("Block {0} has {1} beds; this planting asks for {2}.").format(
					self.block, total, self.beds
				)
			)

	def check_block_not_double_booked(self):
		"""A block holds one planting at a time.

		Two plantings clash when their [planting, uproot] windows overlap, which is
		not the same as their beds summing under capacity.
		"""
		if self.calendar_status == "Cancelled":
			return

		mine_start, mine_end = getdate(self.planting_date), self.end_date()
		others = frappe.get_all(
			"Planting Calendar",
			filters={
				"block": self.block,
				"name": ["!=", self.name or "__new__"],
				"calendar_status": ["in", RESERVING_STATES],
			},
			fields=["name", "variety", "planting_date", "planned_uproot_date",
			        "actual_uproot_date"],
		)
		for o in others:
			other_end = getdate(o.actual_uproot_date or o.planned_uproot_date)
			if mine_start <= other_end and getdate(o.planting_date) <= mine_end:
				frappe.throw(
					_("Block {0} is already occupied by {1} ({2}) from {3} to {4}. "
					  "A block holds one planting at a time.").format(
						self.block, o.name, o.variety,
						frappe.format(o.planting_date, {"fieldtype": "Date"}),
						frappe.format(other_end, {"fieldtype": "Date"}),
					),
					title=_("Block already occupied"),
				)

	# --------------------------------------------------------------------- beds
	def allocate_beds(self):
		"""Attach individual beds so one can be uprooted independently.

		Beds ship grouped by greenhouse, so they are drawn from the ones assigned to
		this block. Rows already carrying an uproot date are preserved.
		"""
		existing = {r.bed: r for r in self.bed_allocation if r.bed}
		want = self.beds or 0

		if len(existing) != want:
			free = frappe.get_all(
				"Bed",
				filters={
					"custom_block": self.block,
					"custom_planting_calendar": ["in", [None, "", self.name or "__new__"]],
				},
				fields=["name", "bed"],
				order_by="bed asc",
				limit=want,
			)
			if len(free) < want and not existing:
				frappe.msgprint(
					_("Block {0} has {1} free beds but this planting needs {2}. "
					  "Allocate the rest manually.").format(self.block, len(free), want),
					indicator="orange", title=_("Not enough beds"),
				)
			keep = [existing[b["name"]] for b in free if b["name"] in existing]
			self.bed_allocation = []
			per_bed = int(round((self.plants or 0) / want)) if want else 0
			for b in free:
				prior = existing.get(b["name"])
				self.append("bed_allocation", {
					"bed": b["name"], "bed_number": b["bed"], "plants": per_bed,
					"bed_status": prior.bed_status if prior else "Planted",
					"actual_uproot_date": prior.actual_uproot_date if prior else None,
					"uproot_reason": prior.uproot_reason if prior else None,
				})
		else:
			per_bed = int(round((self.plants or 0) / want)) if want else 0
			for r in self.bed_allocation:
				if not r.plants:
					r.plants = per_bed

		for r in self.bed_allocation:
			if r.actual_uproot_date:
				r.bed_status = "Uprooted"

		self.beds_uprooted = sum(1 for r in self.bed_allocation if r.actual_uproot_date)
		self.plants_lost_to_uprooting = sum(
			(r.plants or 0) for r in self.bed_allocation if r.actual_uproot_date
		)

	def plants_standing_on(self, when):
		"""Plants still in the ground on a date, after per-bed uprooting.

		Falls back to the header count when no beds are allocated, so the maths
		still works for a planting recorded without bed detail.
		"""
		if not self.bed_allocation:
			return self.plants or 0
		when = getdate(when)
		return sum(
			(r.plants or 0) for r in self.bed_allocation
			if not (r.actual_uproot_date and getdate(r.actual_uproot_date) <= when)
		)

	# ------------------------------------------------------------------ flushes
	def build_flush_projection(self):
		actuals = {
			r.flush_number: (r.is_harvested, r.actual_stems) for r in self.flush_projection
		}
		v = self.version
		planted = getdate(self.planting_date)
		end = self.end_date()
		rounding = v.calendar_rounding_weeks or 0

		offsets = [
			((v.weeks_to_pinch or 0) + (r.weeks_from_pinch or 0) + rounding,
			 r.stems_per_plant or 0)
			for r in sorted(v.flush_schedule, key=lambda r: r.flush_number or 0)
		]

		self.flush_projection = []
		for offset, spp in offsets:
			harvest = planted + datetime.timedelta(weeks=offset)
			if harvest > end:
				break
			year, week = iso_year_week(harvest)
			no = len(self.flush_projection) + 1
			harvested, actual = actuals.get(no, (0, 0))
			# Per-bed, so a bed pulled early stops contributing from that date on.
			standing = self.plants_standing_on(harvest)
			expected = int(round(spp * standing))
			self.append("flush_projection", {
				"flush_number": no, "harvest_date": harvest, "year": year,
				"week_no": week, "stems_per_plant": spp, "expected_stems": expected,
				"plants_standing": standing,
				"is_harvested": harvested, "actual_stems": actual,
				"variance_stems": (actual or 0) - expected if harvested else 0,
			})

	def set_yield(self):
		rows = self.flush_projection
		self.expected_stems_life = sum((r.expected_stems or 0) for r in rows)
		self.actual_stems_harvested = sum(
			(r.actual_stems or 0) for r in rows if r.is_harvested
		)
		harvested_expected = sum(
			(r.expected_stems or 0) for r in rows if r.is_harvested
		)
		self.stems_variance = self.actual_stems_harvested - harvested_expected
		self.harvest_week_family = (
			", ".join(f"wk{w}" for w in sorted({r.week_no for r in rows})) or None
		)

	def check_seedling_source(self):
		if self.seedling_source == "Purchased from Breeder" and not self.supplier:
			frappe.msgprint(_("Record the supplier for a breeder-purchased planting."),
			                indicator="orange", alert=True)
		if self.seedling_source == "In-house Propagation" and not self.propagation_batch:
			frappe.msgprint(_("Link the propagation batch for an in-house planting."),
			                indicator="orange", alert=True)

	def sync_status(self):
		if self.workflow_state:
			self.calendar_status = self.workflow_state

	# ---------------------------------------------------------------- coverage
	def refresh_block_coverage(self):
		if self.block and frappe.db.exists("Block", self.block):
			refresh_coverage(self.block)
		self.sync_bed_records()

	def sync_bed_records(self):
		"""Mirror allocation onto the Bed records so beds are queryable directly."""
		for r in self.bed_allocation:
			if not (r.bed and frappe.db.exists("Bed", r.bed)):
				continue
			frappe.db.set_value("Bed", r.bed, {
				"custom_planting_calendar": self.name,
				"custom_plants": r.plants or 0,
				"custom_bed_status": r.bed_status or "Planted",
				"custom_uproot_date": r.actual_uproot_date,
			}, update_modified=False)

	@frappe.whitelist()
	def uproot_bed(self, bed, on_date, reason=None):
		"""Pull one bed early. Later flushes lose that bed's share."""
		row = next((r for r in self.bed_allocation if r.bed == bed), None)
		if not row:
			frappe.throw(_("Bed {0} is not allocated to this planting.").format(bed))
		if not reason:
			frappe.throw(_("A reason is required to uproot a bed early."))
		row.actual_uproot_date = getdate(on_date)
		row.uproot_reason = reason
		row.bed_status = "Uprooted"
		self.save()
		return {
			"bed": bed,
			"beds_uprooted": self.beds_uprooted,
			"plants_lost": self.plants_lost_to_uprooting,
			"expected_stems_life": self.expected_stems_life,
		}

	# ------------------------------------------------------------- crop cycle
	@frappe.whitelist()
	def create_crop_cycle(self):
		"""An approved calendar becomes a Crop Cycle for its block.

		One cycle per block-planting, not per greenhouse. The doctype's native
		naming is `field:greenhouse`, which would have made every block in a
		greenhouse share one record and overwrite each other's dates; the summer
		flower subclass names the cycle after this calendar instead.
		"""
		if self.calendar_status not in ("Approved", "Planted"):
			frappe.throw(_("Approve the planting calendar first."))
		if self.crop_cycle and frappe.db.exists("Crop Cycle", self.crop_cycle):
			return self.crop_cycle

		v = self.version
		cycle = frappe.new_doc("Crop Cycle")
		cycle.greenhouse = self.greenhouse
		cycle.farm = self.farm
		cycle.company = self.company

		cycle.custom_is_summer_flower_cycle = 1
		cycle.custom_block = self.block
		cycle.custom_planting_calendar = self.name
		cycle.custom_sf_variety = self.variety
		cycle.custom_crop_protocol_version = self.crop_protocol_version
		cycle.custom_market_demand = frappe.db.get_value(
			"Summer Flower Market Demand",
			{"variety": self.variety, "farm": self.farm}, "name")
		cycle.custom_seedling_source = self.seedling_source
		cycle.custom_supplier = self.supplier
		cycle.custom_propagation_batch = self.propagation_batch
		cycle.custom_sf_cycle_status = "Active"
		cycle.custom_planting_date = self.planting_date
		cycle.custom_live_plant_count = self.plants
		cycle.custom_beds_planted = self.beds
		cycle.custom_area_planted_sqm = flt(self.net_area_sqm)
		cycle.custom_planting_density_per_sqm = v.plants_per_sqm_net
		if v.plants_per_sqm_net:
			cycle.plants_per_sqm = v.plants_per_sqm_net
		# The subclass derives pinch, uproot, age, the flush schedule and the
		# per-grade weekly targets from the protocol on save.
		cycle.flags.ignore_permissions = True
		cycle.flags.ignore_mandatory = True
		cycle.insert()

		self.db_set("crop_cycle", cycle.name)
		self.db_set("approved_by", frappe.session.user, update_modified=False)
		self.db_set("approved_on", now_datetime(), update_modified=False)
		return cycle.name


# ---------------------------------------------------------------------------

def refresh_coverage(block):
	"""Recompute a block's occupancy from the plantings standing on it today."""
	today = getdate(nowdate())
	rows = frappe.get_all(
		"Planting Calendar",
		filters={"block": block, "calendar_status": ["in", STANDING_STATES]},
		fields=["name", "variety", "beds", "plants", "planting_date",
		        "planned_uproot_date", "actual_uproot_date"],
	)
	beds = plants = 0
	current = None
	for r in rows:
		end = getdate(r.actual_uproot_date or r.planned_uproot_date)
		if end < today or getdate(r.planting_date) > today:
			continue
		beds += r.beds or 0
		plants += r.plants or 0
		current = r.name

	total = frappe.db.get_value("Block", block, "custom_total_beds") or 0
	frappe.db.set_value("Block", block, {
		"custom_beds_occupied": beds,
		"custom_beds_free": max(0, total - beds),
		"custom_coverage_pct": (beds / total * 100) if total else 0,
		"custom_plants_standing": plants,
		"custom_current_planting": current,
	}, update_modified=False)


def standing_plantings(farm, variety, as_of=None):
	"""Plantings that will still be producing on or after `as_of`."""
	as_of = getdate(as_of or nowdate())
	names = frappe.get_all(
		"Planting Calendar",
		filters={
			"farm": farm,
			"variety": variety,
			"calendar_status": ["not in", ("Cancelled", "Uprooted")],
		},
		pluck="name",
	)
	out = []
	for name in names:
		doc = frappe.get_doc("Planting Calendar", name)
		if doc.end_date() >= as_of:
			out.append(doc)
	return out


def production_by_week(doc):
	"""{(year, week): stems} a planting contributes, actuals overriding projections."""
	out = {}
	for r in doc.flush_projection:
		stems = (r.actual_stems or 0) if r.is_harvested else (r.expected_stems or 0)
		out[(r.year, r.week_no)] = out.get((r.year, r.week_no), 0) + stems
	return out
