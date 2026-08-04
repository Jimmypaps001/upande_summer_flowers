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
from frappe.utils import add_days, cint, flt, getdate, now_datetime, nowdate

WEEKS_PER_YEAR = 52


class CropProtocolVersion(Document):
	def guard_snapshot_only(self):
		"""A version is written by approving a Crop Protocol, and by nothing else.

		It is the history: a full copy of the protocol as it stood when a change was
		approved, and the thing every plan, planting and crop cycle pins to. Letting
		it be edited afterwards would rewrite what an approved plan says it was built
		on, which is the one guarantee this doctype exists to give.

		Migrations and patches are let through, because they have to be able to carry
		old data forward.
		"""
		if self.flags.from_protocol_snapshot or self.flags.ignore_protocol_lock:
			return
		if (frappe.flags.in_migrate or frappe.flags.in_patch or frappe.flags.in_install
				or frappe.flags.in_test):
			return
		frappe.throw(
			_("{0} is a snapshot and cannot be edited. Change the protocol on Crop "
			  "Protocol {1} and approve it — that writes a new version and leaves "
			  "this one as the record of what came before.").format(
				self.name, self.crop_protocol or ""),
			title=_("Read-only history"))

	def validate(self):
		self.guard_snapshot_only()
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
		# Superseded is terminal and is set by the successor's activation, not by the
		# workflow. Copying workflow_state over it resurrects a retired version --
		# which left two versions Active at once and made resolve_version ambiguous.
		if self.version_status == "Superseded":
			return
		if self.workflow_state and self.workflow_state in (
			"Draft", "Pending Approval", "Active"
		):
			self.version_status = self.workflow_state

	# ------------------------------------------------------------------ geometry
	def set_geometry(self):
		"""Everything geometric comes off net bed area.

		Gross block area and the net:gross ratio are gone: the farm plans in the
		ground the crop occupies, and carrying both invited every per-hectare figure
		to be quoted against whichever one the reader assumed.
		"""
		if not self.plants_per_sqm_net:
			frappe.throw(_("Plants per m² of bed (net) is required."))

		self.sqm_net_per_bed = (self.plants_per_bed or 0) / self.plants_per_sqm_net

		# The minimum planting is an area. Beds are whole, so it rounds up to one.
		beds = 0
		if self.min_planting_area_sqm and self.sqm_net_per_bed:
			beds = int(math.ceil(flt(self.min_planting_area_sqm)
			                     / flt(self.sqm_net_per_bed)))
		self.min_planting_beds_derived = max(beds, 1) if self.min_planting_area_sqm else 0
		self.min_planting_plants = (self.min_planting_beds_derived or 0) \
			* (self.plants_per_bed or 0)

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
			row.harvest_week_of_year = "+{0}".format(row.weeks_from_planting)

		self.flush_schedule = rows

	def set_cycle_totals(self):
		rows = self.flush_schedule
		self.total_flushes = len(rows)
		self.total_stems_per_plant_life = sum((r.stems_per_plant or 0) for r in rows)

		last = max((r.weeks_from_pinch or 0) for r in rows) if rows else 0
		self.total_weeks_in_ground = (self.weeks_to_pinch or 0) + last

		first = min((r.weeks_from_pinch or 0) for r in rows) if rows else 0
		# Pinch plus the first flush, and nothing else. A rounding allowance used to
		# be added here, which is why Aster read 21 weeks against a protocol that
		# says 7 + 13.
		self.first_harvest_offset_weeks = (self.weeks_to_pinch or 0) + first

		# Where the flush interval divides the year, a planting returns to the same
		# few weeks every year until it is uprooted.
		self.harvest_weeks_per_year = (
			round(WEEKS_PER_YEAR / self.flush_interval_weeks)
			if self.flush_interval_weeks else 0
		)

		years = (self.total_weeks_in_ground or 0) / WEEKS_PER_YEAR
		self.flushes_per_year = (self.total_flushes / years) if years else 0

		self.life_expectancy_years = years

		# Per hectare means per hectare of BED. Quoting it on gross block area is
		# what the net:gross ratio was for, and that is gone: one area, stated.
		plants_per_ha = (self.plants_per_sqm_net or 0) * 10_000
		self.stems_per_ha_life = (self.total_stems_per_plant_life or 0) * plants_per_ha
		self.stems_per_ha_year = (self.stems_per_ha_life / years) if years else 0

		# A target yield and a computed one rarely agree; show the gap instead of
		# quietly preferring one.
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
		# then the ramp to full capacity. Hardening is not in here -- it belongs to
		# the cutting-to-harvest path, where the cutting goes to the field instead.
		self.ms_establishment_weeks = (
			(self.weeks_on_tray or 0) + (self.weeks_on_pot or 0) + (self.ramp_weeks or 0)
		)
		self.cutting_to_harvest_weeks = (
			(self.hardening_weeks or 0) + (self.weeks_to_pinch or 0)
			+ (self.flush_interval_weeks or 0)
		)
		self.plants_per_sqm_bench = (self.pots_per_sqm or 0) * (self.plants_per_pot or 0)

		cycles = self.max_multiplication_cycles or 0
		factor = self.multiplication_factor_per_cycle or 0
		self.max_multiplication_factor = 1 + (cycles * factor)
		self.lead_time_weeks = self.lead_time_for_cycles(cycles)

	def ramp_ratios(self):
		"""The climb to full cutting capacity, as one ratio per week.

		A new pool does not cut at its full rate the week it starts. ramp_profile
		records the shape ("25,50,75,100"); ramp_weeks its length. Falls back to
		weeks_to_max_pc where only that was filled in, so a protocol written before
		the profile existed still ramps instead of jumping to 100%.
		"""
		from upande_summer_flowers.summer_flowers.lifecycle_sim import parse_ramp

		return parse_ramp(self.ramp_profile,
		                  cint(self.ramp_weeks) or cint(self.weeks_to_max_pc))

	def weeks_tc_to_first_cut(self):
		"""Weeks from a TC plantlet arriving to the first cutting off it.

		Tray and pot only. The ramp is not waited through -- it is cut through, at
		the reducing rate ramp_profile describes, which is the whole point of having
		a profile. Hardening is not in here either: it belongs to the cutting that
		goes to the field, not to the mother plant that stays on the bench. This is
		the same definition ms_establishment_weeks already documents, less the ramp.
		"""
		return (self.weeks_on_tray or 0) + (self.weeks_on_pot or 0)

	def lead_time_for_cycles(self, cycles):
		"""Weeks from receiving TC plantlets to the first cutting.

		With no build-up it is a single establishment. With build-up the multiplied
		generation needs its own establishment on top, so there is no cheap middle
		option between 0 and 1 cycle.
		"""
		est = self.weeks_tc_to_first_cut()
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
		"""[(weeks after planting the harvest lands, stems per plant), ...]"""
		return [
			(
				(self.weeks_to_pinch or 0) + (r.weeks_from_pinch or 0),
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
def current_version(variety, farm):
	"""The Active version a plan for this crop at this farm should start from.

	`refresh_current_flag` keeps exactly one Active version per (protocol, farm)
	flagged as current, so this is a lookup and not a judgement. It orders by the
	flag first so a site where the flag was never refreshed still answers with
	the newest effective version rather than an arbitrary row.
	"""
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
