# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""A versioned, per-farm parameter set hanging off a variety's Crop Protocol.

Crop Protocol stays the variety master. Amendments create a new version record
rather than editing in place, so a crop cycle keeps pointing at the parameters it
actually ran under. Superseding is what closes the previous version's effective
window -- nothing is overwritten.
"""

import math

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, getdate, now_datetime, nowdate

WEEKS_PER_YEAR = 52


class CropProtocolVersion(Document):
	def validate(self):
		self.set_variety()
		self.set_geometry()
		self.set_flush_schedule()
		self.set_cycle_totals()
		self.set_motherstock()
		self.set_grades_and_losses()
		self.check_effective_from()
		self.sync_status()

	def on_update(self):
		# Frappe workflows only move workflow_state; they do not call into the
		# controller, so detect the Draft/Pending -> Active crossing ourselves.
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
		if self.workflow_state and self.workflow_state in (
			"Draft", "Pending Approval", "Active", "Superseded"
		):
			self.version_status = self.workflow_state

	# ------------------------------------------------------------------ geometry
	def set_geometry(self):
		if not self.plants_per_sqm_net:
			frappe.throw(_("Plants per m² (net) is required."))
		if not self.net_gross_ratio:
			self.net_gross_ratio = 0.8

		self.sqm_net_per_bed = (self.plants_per_bed or 0) / self.plants_per_sqm_net
		self.sqm_gross_per_bed = self.sqm_net_per_bed / self.net_gross_ratio
		self.plants_per_sqm_gross = self.plants_per_sqm_net * self.net_gross_ratio
		self.min_planting_plants = (self.min_planting_beds or 0) * (self.plants_per_bed or 0)

	# --------------------------------------------------------------------- flush
	def set_flush_schedule(self):
		rows = sorted(self.flush_schedule, key=lambda r: r.flush_number or 0)
		interval = self.flush_interval_weeks or 0

		for idx, row in enumerate(rows, start=1):
			row.idx = idx
			row.flush_number = idx
			if not row.weeks_from_pinch and interval:
				row.weeks_from_pinch = interval * idx
			row.weeks_from_planting = (self.weeks_to_pinch or 0) + (row.weeks_from_pinch or 0)
			row.harvest_week_of_year = "+{0}".format(
				row.weeks_from_planting + (self.calendar_rounding_weeks or 0)
			)

		self.flush_schedule = rows

	def set_cycle_totals(self):
		rows = self.flush_schedule
		self.total_flushes = len(rows)
		self.total_stems_per_plant_life = sum((r.stems_per_plant or 0) for r in rows)

		last = max((r.weeks_from_pinch or 0) for r in rows) if rows else 0
		self.total_weeks_in_ground = (self.weeks_to_pinch or 0) + last

		first = min((r.weeks_from_pinch or 0) for r in rows) if rows else 0
		self.first_harvest_offset_weeks = (
			(self.weeks_to_pinch or 0) + first + (self.calendar_rounding_weeks or 0)
		)

		# Where the flush interval divides the year, a planting returns to the same
		# few weeks every year until it is uprooted.
		self.harvest_weeks_per_year = (
			round(WEEKS_PER_YEAR / self.flush_interval_weeks)
			if self.flush_interval_weeks else 0
		)

		years = (self.total_weeks_in_ground or 0) / WEEKS_PER_YEAR
		self.flushes_per_year = (self.total_flushes / years) if years else 0

		plants_per_ha = (self.plants_per_sqm_gross or 0) * 10_000
		self.stems_per_ha_life = (self.total_stems_per_plant_life or 0) * plants_per_ha
		self.stems_per_ha_year = (self.stems_per_ha_life / years) if years else 0

	# --------------------------------------------------------------- motherstock
	def set_motherstock(self):
		self.establishment_weeks = (
			(self.weeks_on_tray or 0) + (self.weeks_on_pot or 0)
			+ (self.weeks_to_max_pc or 0) + (self.hardening_weeks or 0)
		)
		self.plants_per_sqm_bench = (self.pots_per_sqm or 0) * (self.plants_per_pot or 0)

		cycles = self.max_multiplication_cycles or 0
		factor = self.multiplication_factor_per_cycle or 0
		self.max_multiplication_factor = 1 + (cycles * factor)
		self.lead_time_weeks = self.lead_time_for_cycles(cycles)

	def lead_time_for_cycles(self, cycles):
		"""Weeks from receiving TC plantlets to a motherstock at max PC.

		With no build-up it is a single establishment. With build-up the multiplied
		generation needs its own establishment on top, so there is no cheap middle
		option between 0 and 1 cycle.
		"""
		est = self.establishment_weeks or 0
		if not cycles:
			return est
		return est + (cycles * (self.cycle_time_weeks or 0)) + est

	def tc_plants_for(self, mother_plants, cycles=None):
		if cycles is None:
			cycles = self.max_multiplication_cycles or 0
		factor = 1 + (cycles * (self.multiplication_factor_per_cycle or 0))
		return (mother_plants / factor) if factor else mother_plants

	# ---------------------------------------------------------- grades & losses
	def set_grades_and_losses(self):
		self.grade_total_pct = sum((r.allocation_pct or 0) for r in self.grade_allocation)
		if self.grade_allocation and abs(self.grade_total_pct - 100) > 0.01:
			frappe.msgprint(
				_("Grade allocation totals {0}%, not 100%.").format(self.grade_total_pct),
				indicator="orange", title=_("Check grade split"),
			)

		rooting = (self.rooting_success_pct or 100) / 100
		field = (self.field_establishment_pct or 100) / 100
		survival = rooting * field
		self.cuttings_per_plant_required = (1 / survival) if survival else 1

	# ------------------------------------------------------------------ helpers
	def flush_offsets(self):
		"""[(weeks after planting the harvest lands, stems per plant), ...]

		Includes the calendar rounding allowance, so offsets are usable directly
		against a Monday-based planning grid.
		"""
		rounding = self.calendar_rounding_weeks or 0
		return [
			(
				(self.weeks_to_pinch or 0) + (r.weeks_from_pinch or 0) + rounding,
				r.stems_per_plant or 0,
			)
			for r in sorted(self.flush_schedule, key=lambda r: r.flush_number or 0)
		]

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
		"""Cuttings to stick for a target number of standing plants.

		Applies rooting and field losses, then the cutting reject rate on top.
		"""
		needed = plants * (self.cuttings_per_plant_required or 1)
		reject = (self.cutting_reject_pct or 0) / 100
		if reject and reject < 1:
			needed = needed / (1 - reject)
		return int(math.ceil(needed))

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
			# a cycle planted on any date resolves to exactly one version.
			prior.db_set(
				"effective_to", add_days(getdate(self.effective_from), -1),
				update_modified=False,
			)

		self.refresh_current_flag()

	@frappe.whitelist()
	def create_amendment(self, change_reason=None, effective_from=None):
		"""Copy this version into a new Draft for amendment.

		In-progress cycles keep pointing at this version. They only move if
		explicitly migrated.
		"""
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
def resolve_version(crop_protocol, farm, on_date=None):
	"""The version in force for a variety at a farm on a given date.

	Planning uses today; a crop cycle uses its planting date, which is what keeps
	history attached to the parameters it actually ran under.
	"""
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
