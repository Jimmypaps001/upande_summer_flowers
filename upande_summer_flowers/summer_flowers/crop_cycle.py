# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Summer flower behaviour for Crop Cycle.

Crop Cycle belongs to upande_agriculture and carries 61 live records for other
crops, so this subclass does two things and nothing else:

  * it names summer flower cycles per block-planting instead of per greenhouse,
    because the native ``autoname = field:greenhouse`` would collapse every
    block in a greenhouse into a single record, and all twelve summer flower
    blocks at Karen sit in one greenhouse;
  * it runs the summer flower lifecycle after the native ``validate``, gated on
    ``custom_is_summer_flower_cycle`` so nothing else changes behaviour.

The native rollups derive ``area_planted``/``number_of_plants`` from bed ranges,
which a summer flower cycle does not use. Rather than fight that, the summer
flower geometry lives in its own ``custom_*`` fields and the native ones are
left to the bed-range crops.
"""

import datetime
import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, now_datetime, nowdate

from upande_agriculture.upande_agriculture.doctype.crop_cycle.crop_cycle import CropCycle

from upande_summer_flowers.summer_flowers.planning import iso_year_week

APPROVER_ROLES = ("Farm Manager", "Agriculture Manager", "System Manager")
TARGET_ROLES = ("Production Manager", "Farm Manager", "Agriculture Manager",
                "System Manager")

ENDED_STATES = ("Ended",)


def _has_role(roles=APPROVER_ROLES):
	return bool(set(roles) & set(frappe.get_roles(frappe.session.user)))


class EffectiveProtocol:
	"""The protocol a cycle actually runs on: the version, plus cycle overrides.

	Overrides are deliberately read through this one object so that every
	derived date and yield uses the same numbers, and so the master protocol is
	never mutated to express a single planting's deviation.
	"""

	SCALARS = (
		("weeks_to_pinch", "custom_ovr_weeks_to_pinch"),
		("flush_interval_weeks", "custom_ovr_flush_interval_weeks"),
		("first_harvest_offset_weeks", "custom_ovr_first_harvest_offset_weeks"),
		("total_weeks_in_ground", "custom_ovr_total_weeks_in_ground"),
	)

	def __init__(self, version, cycle=None):
		self.version = version
		self.cycle = cycle
		self.overridden = []
		for attr, ovr_field in self.SCALARS:
			base = cint(version.get(attr))
			val = base
			if cycle is not None and cint(cycle.get("custom_has_protocol_override")):
				o = cint(cycle.get(ovr_field))
				if o:
					val = o
					if o != base:
						self.overridden.append(attr)
			setattr(self, attr, val)

	def flush_rows(self):
		"""(flush_number, weeks_from_pinch, stems_per_plant), override winning."""
		src = None
		if self.cycle is not None and cint(self.cycle.get("custom_has_protocol_override")):
			rows = self.cycle.get("custom_ovr_flush_schedule") or []
			if rows:
				src = rows
				if "flush_schedule" not in self.overridden:
					self.overridden.append("flush_schedule")
		if src is None:
			src = self.version.get("flush_schedule") or []
		return [
			(cint(r.flush_number), cint(r.weeks_from_pinch), flt(r.stems_per_plant))
			for r in sorted(src, key=lambda r: cint(r.flush_number))
		]

	def snapshot(self):
		"""The master protocol's own values, for variance comparison."""
		return {
			"crop_protocol_version": self.version.name,
			"captured_on": str(now_datetime()),
			**{attr: cint(self.version.get(attr)) for attr, _f in self.SCALARS},
			"flush_schedule": [
				{"flush_number": cint(r.flush_number),
				 "weeks_from_pinch": cint(r.weeks_from_pinch),
				 "stems_per_plant": flt(r.stems_per_plant)}
				for r in sorted(self.version.get("flush_schedule") or [],
				                key=lambda r: cint(r.flush_number))
			],
		}


class SummerFlowerCropCycle(CropCycle):
	# --------------------------------------------------------------- naming
	def autoname(self):
		"""Name summer flower cycles per block-planting.

		Frappe calls this before applying the doctype's own ``field:greenhouse``
		rule and only falls back to that rule when ``self.name`` is still unset,
		so leaving the name alone for other crops preserves their naming exactly.
		"""
		if not self.get("custom_is_summer_flower_cycle"):
			return
		if self.get("custom_planting_calendar"):
			self.name = "CC-{0}".format(self.custom_planting_calendar)
			return
		# A block is replanted many times over, so the block alone is not unique;
		# the planting week is what separates one cycle from its successor.
		if self.get("custom_block") and self.get("custom_planting_date"):
			y, w = iso_year_week(getdate(self.custom_planting_date))
			self.name = "CC-{0}-{1}W{2:02d}".format(self.custom_block, y, w)

	# ------------------------------------------------------------- validate
	def validate(self):
		super().validate()
		if not self.get("custom_is_summer_flower_cycle"):
			return

		self._version = self._resolve_version()
		self.protocol = EffectiveProtocol(self._version, self)

		self.check_override_approval()
		self.sync_block_geometry()
		self.set_dates()
		self.set_plant_age()
		self.check_uproot_deviation()
		self.build_flush_schedule()
		self.roll_up_harvest()
		self.build_weekly_targets()
		self.sync_cycle_status()
		self.compute_royalty()

	def _resolve_version(self):
		if not self.get("custom_crop_protocol_version"):
			frappe.throw(_("A summer flower cycle needs a Crop Protocol Version."))
		return frappe.get_cached_doc("Crop Protocol Version",
		                            self.custom_crop_protocol_version)

	# ------------------------------------------------------------- geometry
	def sync_block_geometry(self):
		"""Block area is the ceiling; a planting need not fill it.

		Gross area is read from the block on every save rather than copied once,
		because a block's gross area legitimately changes over time.
		"""
		if not self.get("custom_block"):
			return
		ha = flt(frappe.db.get_value("Block", self.custom_block,
		                             "custom_gross_area_ha"))
		gross_sqm = ha * 10_000
		self.custom_block_gross_area_sqm = gross_sqm

		if not flt(self.custom_planting_density_per_sqm):
			self.custom_planting_density_per_sqm = flt(
				self._version.plants_per_sqm_net)

		if flt(self.custom_area_planted_sqm) and gross_sqm and \
				flt(self.custom_area_planted_sqm) > gross_sqm + 0.5:
			frappe.throw(_(
				"Planted area {0} m² exceeds block {1}'s gross area of {2} m². "
				"A planting can fill the block but cannot exceed it."
			).format(flt(self.custom_area_planted_sqm), self.custom_block,
			         round(gross_sqm, 2)))

		self.custom_area_utilisation_pct = (
			flt(self.custom_area_planted_sqm) * 100.0 / gross_sqm if gross_sqm else 0
		)
		if not cint(self.custom_live_plant_count) and flt(self.custom_area_planted_sqm):
			self.custom_live_plant_count = int(round(
				flt(self.custom_area_planted_sqm) *
				flt(self.custom_planting_density_per_sqm)))

	# ---------------------------------------------------------------- dates
	def set_dates(self):
		"""Planned dates from the effective protocol; actuals override forward.

		Once an actual pinch date is recorded, every downstream date is measured
		from it rather than from the planned pinch, so a late pinch moves the
		whole remaining cycle instead of silently compressing it.
		"""
		if not self.get("custom_planting_date"):
			return
		planted = getdate(self.custom_planting_date)
		p = self.protocol

		self.custom_planned_pinch_date = add_days(planted, 7 * p.weeks_to_pinch)

		# The pinch is the anchor for everything after it.
		anchor = getdate(self.custom_actual_pinch_date) \
			if self.get("custom_actual_pinch_date") else getdate(self.custom_planned_pinch_date)
		self._pinch_anchor = anchor
		# Life is quoted from planting, so a shifted pinch shifts the end too.
		drift = (anchor - getdate(self.custom_planned_pinch_date)).days
		self.custom_planned_uproot_date = add_days(
			planted, 7 * p.total_weeks_in_ground + drift)

	def set_plant_age(self):
		"""Age today, or age at uprooting once that has actually happened.

		An uproot booked for a future date must not age the crop forward: the
		plants are still in the ground and their age is still today's.
		"""
		if not self.get("custom_planting_date"):
			return
		end = getdate(nowdate())
		if self.get("custom_actual_uproot_date"):
			end = min(end, getdate(self.custom_actual_uproot_date))
		self.custom_plant_age_weeks = max(
			0, (end - getdate(self.custom_planting_date)).days // 7)

	# ------------------------------------------------------------ uprooting
	def check_uproot_deviation(self):
		"""An uproot off the planned date needs Farm Manager sign-off and a reason."""
		if not self.get("custom_actual_uproot_date"):
			self.custom_uproot_deviation_days = 0
			return
		if not self.get("custom_planned_uproot_date"):
			return
		dev = (getdate(self.custom_actual_uproot_date)
		       - getdate(self.custom_planned_uproot_date)).days
		self.custom_uproot_deviation_days = dev
		if dev == 0:
			return
		if not (self.get("custom_uproot_deviation_reason") or "").strip():
			frappe.throw(_(
				"Uprooting on {0} is {1} days {2} the planned {3}. A deviation "
				"reason is required."
			).format(self.custom_actual_uproot_date, abs(dev),
			         "before" if dev < 0 else "after",
			         self.custom_planned_uproot_date))
		if not self.get("custom_uproot_approved_by"):
			if not _has_role():
				frappe.throw(_(
					"Uprooting {0} days off plan needs Farm Manager approval. Use "
					"Approve Uproot Deviation."
				).format(abs(dev)))
			self.custom_uproot_approved_by = frappe.session.user
			self.custom_uproot_approved_on = now_datetime()

	# ------------------------------------------------------------- override
	def check_override_approval(self):
		"""A cycle-level protocol override needs justification and sign-off.

		The master protocol values are snapshotted at approval so the deviation
		stays comparable even if the protocol is later amended.
		"""
		if not cint(self.get("custom_has_protocol_override")):
			self.custom_protocol_original_values = None
			self.custom_override_approved_by = None
			self.custom_override_approved_on = None
			return
		if not (self.get("custom_override_justification") or "").strip():
			frappe.throw(_("A protocol override needs a justification."))
		if not self.get("custom_override_approved_by"):
			if not _has_role():
				frappe.throw(_(
					"Overriding the protocol for this cycle needs Farm Manager "
					"approval. Use Approve Protocol Override."))
			self.custom_override_approved_by = frappe.session.user
			self.custom_override_approved_on = now_datetime()
		if not self.get("custom_protocol_original_values"):
			self.custom_protocol_original_values = json.dumps(
				self.protocol.snapshot(), indent=1)

	# ---------------------------------------------------- rolling forecast
	def build_flush_schedule(self):
		"""Rebuild the flush projection, preserving anything already harvested."""
		if not self.get("custom_planting_date"):
			return
		actuals = {
			cint(r.flush_number): (cint(r.is_harvested), cint(r.actual_stems))
			for r in (self.get("custom_flush_schedule") or [])
		}
		anchor = getattr(self, "_pinch_anchor", None) or getdate(
			self.custom_planned_pinch_date or self.custom_planting_date)
		end = getdate(self.custom_actual_uproot_date or self.custom_planned_uproot_date) \
			if (self.get("custom_actual_uproot_date")
			    or self.get("custom_planned_uproot_date")) else None
		rounding = cint(self._version.calendar_rounding_weeks)
		plants = cint(self.custom_live_plant_count)

		self.set("custom_flush_schedule", [])
		for number, weeks_from_pinch, spp in self.protocol.flush_rows():
			harvest = add_days(anchor, 7 * (weeks_from_pinch + rounding))
			if end and getdate(harvest) > end:
				break
			y, w = iso_year_week(getdate(harvest))
			no = len(self.get("custom_flush_schedule")) + 1
			harvested, actual = actuals.get(no, (0, 0))
			expected = int(round(spp * plants))
			self.append("custom_flush_schedule", {
				"flush_number": no, "harvest_date": harvest, "year": y,
				"week_no": w, "stems_per_plant": spp, "expected_stems": expected,
				"plants_standing": plants, "is_harvested": harvested,
				"actual_stems": actual,
				"variance_stems": (actual - expected) if harvested else 0,
			})

	def roll_up_harvest(self):
		"""Current flush, next harvest, and harvested-versus-expected."""
		rows = self.get("custom_flush_schedule") or []
		done = [r for r in rows if cint(r.is_harvested)]
		todo = [r for r in rows if not cint(r.is_harvested)]

		self.custom_total_harvested_stems = sum(cint(r.actual_stems) for r in done)
		self.custom_expected_stems_life = sum(cint(r.expected_stems) for r in rows)
		# Variance is against what the harvested flushes were expected to yield,
		# not against the whole life, or an early cycle always looks catastrophic.
		self.custom_harvest_variance_stems = (
			self.custom_total_harvested_stems
			- sum(cint(r.expected_stems) for r in done)
		)
		self.custom_current_flush_number = cint(done[-1].flush_number) if done else 0
		self.custom_last_harvest_date = done[-1].harvest_date if done else None
		self.custom_flushes_remaining = len(todo)
		self.custom_expected_stems_remaining = sum(cint(r.expected_stems) for r in todo)
		if todo:
			self.custom_next_harvest_date = todo[0].harvest_date
			self.custom_expected_stems_next_flush = cint(todo[0].expected_stems)
		else:
			self.custom_next_harvest_date = None
			self.custom_expected_stems_next_flush = 0

	# ------------------------------------------------------ weekly targets
	def build_weekly_targets(self):
		"""Expected stems per harvest week per grade.

		Only harvest weeks get rows: between flushes the expectation is zero by
		definition, and storing 111 zero rows per cycle would bury the real ones.
		"""
		grades = [(g.grade, flt(g.allocation_pct))
		          for g in (self.get("custom_grade_split") or [])]
		if not grades:
			grades = [(g.grade, flt(g.allocation_pct))
			          for g in (self._version.get("grade_allocation") or [])]
			self.set("custom_grade_split", [])
			for g, pct in grades:
				self.append("custom_grade_split", {"grade": g, "allocation_pct": pct})
		if not grades:
			self.set("custom_weekly_targets", [])
			self.custom_target_total_stems = 0
			return

		# Actuals and hand-set targets must survive the rebuild, but an
		# auto-defaulted target must not: it would otherwise keep the old
		# expectation after a protocol override changed the yields. A target is
		# treated as hand-set only when it differs from the expectation it was
		# defaulted from.
		prev = {
			(cint(r.year), cint(r.week_no), r.grade): (
				cint(r.target_stems), cint(r.expected_stems),
				cint(r.actual_stems), r.notes)
			for r in (self.get("custom_weekly_targets") or [])
		}
		self.set("custom_weekly_targets", [])
		for fl in (self.get("custom_flush_schedule") or []):
			for grade, pct in grades:
				expected = int(round(cint(fl.expected_stems) * pct / 100.0))
				key = (cint(fl.year), cint(fl.week_no), grade)
				old_target, old_expected, actual, notes = prev.get(key, (0, 0, 0, None))
				manual = bool(old_target) and old_target != old_expected
				target = old_target if manual else expected
				self.append("custom_weekly_targets", {
					"year": fl.year, "week_no": fl.week_no,
					"week_start_date": _monday(cint(fl.year), cint(fl.week_no)),
					"flush_number": fl.flush_number, "grade": grade,
					"expected_stems": expected,
					"target_stems": target,
					"actual_stems": actual,
					"variance_stems": (actual - target) if actual else 0,
					"notes": notes,
				})
		self.custom_target_total_stems = sum(
			cint(r.target_stems) for r in self.get("custom_weekly_targets"))

		# Any change to the numbers invalidates a previous validation.
		if self.custom_targets_status == "Validated" and not self.is_new():
			before = self.get_doc_before_save()
			if before and cint(before.get("custom_target_total_stems")) != \
					cint(self.custom_target_total_stems):
				self.custom_targets_status = "Draft"
				self.custom_targets_validated_by = None
				self.custom_targets_validated_on = None

	# --------------------------------------------------------------- status
	def sync_cycle_status(self):
		"""Active / Replanting / Partially Uprooted / Ended, from the facts."""
		if self.get("custom_actual_uproot_date") and \
				getdate(self.custom_actual_uproot_date) <= getdate(nowdate()):
			self.custom_sf_cycle_status = "Ended"
			return
		beds = self.get("custom_planting_calendar") and frappe.get_all(
			"Planting Calendar Bed",
			filters={"parent": self.custom_planting_calendar},
			fields=["bed_status"]) or []
		if beds and any(b.bed_status == "Uprooted" for b in beds) and \
				any(b.bed_status != "Uprooted" for b in beds):
			self.custom_sf_cycle_status = "Partially Uprooted"
			return
		if self.get("last_replanting_date") and \
				getdate(self.last_replanting_date) >= add_days(getdate(nowdate()), -14):
			self.custom_sf_cycle_status = "Replanting"
			return
		if self.custom_sf_cycle_status in (None, "", "Ended"):
			self.custom_sf_cycle_status = "Active"

	# -------------------------------------------------------------- royalty
	def compute_royalty(self):
		"""Royalty is on what was actually cut, not on what was forecast."""
		if not cint(self.get("custom_royalty_applicable")):
			self.custom_total_royalty_payable = 0
			return
		if not self.get("custom_royalty_currency"):
			self.custom_royalty_currency = frappe.db.get_value(
				"Company", self.company, "default_currency")
		self.custom_total_royalty_payable = (
			flt(self.custom_royalty_rate) * cint(self.custom_total_harvested_stems))

	# ------------------------------------------------------------- actions
	@frappe.whitelist()
	def approve_protocol_override(self):
		if not _has_role():
			frappe.throw(_("Only a Farm Manager can approve a protocol override."))
		if not cint(self.get("custom_has_protocol_override")):
			frappe.throw(_("This cycle has no protocol override to approve."))
		if not (self.get("custom_override_justification") or "").strip():
			frappe.throw(_("A protocol override needs a justification."))
		self.custom_override_approved_by = frappe.session.user
		self.custom_override_approved_on = now_datetime()
		self.save()
		return self.custom_override_approved_on

	@frappe.whitelist()
	def approve_uproot_deviation(self):
		if not _has_role():
			frappe.throw(_("Only a Farm Manager can approve an uprooting deviation."))
		if not (self.get("custom_uproot_deviation_reason") or "").strip():
			frappe.throw(_("Record the deviation reason first."))
		self.custom_uproot_approved_by = frappe.session.user
		self.custom_uproot_approved_on = now_datetime()
		self.save()
		return self.custom_uproot_approved_on

	@frappe.whitelist()
	def submit_targets_for_validation(self):
		if not self.get("custom_weekly_targets"):
			frappe.throw(_("There are no weekly targets to validate."))
		self.custom_targets_status = "Pending Validation"
		self.custom_targets_rejection_reason = None
		self.save()
		return self.custom_targets_status

	@frappe.whitelist()
	def validate_targets(self):
		"""Production Manager signs off the weekly targets."""
		if not _has_role(TARGET_ROLES):
			frappe.throw(_("Only a Production Manager can validate weekly targets."))
		if self.custom_targets_status != "Pending Validation":
			frappe.throw(_("Submit the targets for validation first."))
		self.custom_targets_status = "Validated"
		self.custom_targets_validated_by = frappe.session.user
		self.custom_targets_validated_on = now_datetime()
		self.save()
		return self.custom_targets_status

	@frappe.whitelist()
	def reject_targets(self, reason=None):
		if not _has_role(TARGET_ROLES):
			frappe.throw(_("Only a Production Manager can reject weekly targets."))
		if not (reason or "").strip():
			frappe.throw(_("A rejection reason is required."))
		self.custom_targets_status = "Rejected"
		self.custom_targets_rejection_reason = reason
		self.save()
		return self.custom_targets_status

	@frappe.whitelist()
	def record_harvest(self, flush_number, actual_stems, harvest_date=None):
		"""Close a flush with what was actually cut and roll the forecast on."""
		row = next((r for r in (self.get("custom_flush_schedule") or [])
		            if cint(r.flush_number) == cint(flush_number)), None)
		if not row:
			frappe.throw(_("Flush {0} is not on this cycle.").format(flush_number))
		row.is_harvested = 1
		row.actual_stems = cint(actual_stems)
		if harvest_date:
			row.harvest_date = getdate(harvest_date)
			row.year, row.week_no = iso_year_week(getdate(harvest_date))
		row.variance_stems = cint(actual_stems) - cint(row.expected_stems)
		self.save()
		return {
			"current_flush": self.custom_current_flush_number,
			"next_harvest_date": str(self.custom_next_harvest_date or ""),
			"expected_next": self.custom_expected_stems_next_flush,
			"harvested_total": self.custom_total_harvested_stems,
			"variance": self.custom_harvest_variance_stems,
		}

	@frappe.whitelist()
	def record_cuttings(self, quantity, taken_on=None):
		"""Cuttings taken off this cycle for propagation.

		Cuttings do not reduce the plant count -- the plant stays in the ground --
		so this is tracked separately from uprooting.
		"""
		qty = cint(quantity)
		if qty <= 0:
			frappe.throw(_("Cutting quantity must be positive."))
		self.custom_cuttings_taken = cint(self.custom_cuttings_taken) + qty
		self.add_comment(
			"Info", _("{0} cuttings taken for propagation on {1}.").format(
				qty, getdate(taken_on or nowdate())))
		self.save()
		return self.custom_cuttings_taken

	@frappe.whitelist()
	def end_cycle(self, actual_uproot_date, reason=None):
		"""Record the actual uprooting and recalculate the affected metrics."""
		self.custom_actual_uproot_date = getdate(actual_uproot_date)
		if reason:
			self.custom_uproot_deviation_reason = reason
		self.save()
		return {
			"status": self.custom_sf_cycle_status,
			"deviation_days": self.custom_uproot_deviation_days,
			"plant_age_weeks": self.custom_plant_age_weeks,
			"flushes_remaining": self.custom_flushes_remaining,
			"expected_stems_remaining": self.custom_expected_stems_remaining,
		}


def _monday(year, week):
	try:
		return datetime.date.fromisocalendar(year, week, 1)
	except ValueError:
		return None
