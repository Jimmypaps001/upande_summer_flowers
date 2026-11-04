# Copyright (c) 2026, James Kiruga and contributors
"""Summer flower behaviour for Crop Cycle."""

import datetime
import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, now_datetime, nowdate
from upande_summer_flowers.summer_flowers import settings as _settings

from upande_agriculture.upande_agriculture.doctype.crop_cycle.crop_cycle import CropCycle

from upande_summer_flowers.summer_flowers.planning import iso_year_week


ENDED_STATES = ("Ended",)


def _has_role(roles=None):
	# Who counts is set in Summer Flower Settings, not here.
	roles = _settings.approver_roles() if roles is None else roles
	return bool(set(roles) & set(frappe.get_roles(frappe.session.user)))


class EffectiveProtocol:
	"""The protocol a cycle actually runs on: the version, plus cycle overrides."""

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
	# ----------------------------------------------------------- one variety
	def before_validate(self):
		self.settle_crop_class()
		"""Keep custom_sf_variety filled from the native variety.

		Both are Link fields to Item, both were labelled "Variety", and both were
		visible, so the form asked the same question twice. custom_sf_variety is
		now hidden and derived: crop_cycle_api filters on it directly, so it has
		to hold a value even though nobody types one any more.
		"""
		self.settle_scope()
		self.fill_farm_from_block()
		if self.get("variety") and not self.get("custom_sf_variety"):
			self.custom_sf_variety = self.get("variety")
		elif self.get("custom_sf_variety") and not self.get("variety"):
			self.variety = self.get("custom_sf_variety")
		parent = getattr(super(), "before_validate", None)
		if parent:
			parent()

	def settle_crop_class(self):
		"""Crop Class is the one question asked first; the rest follows it."""
		cls = self.get("custom_crop_class")
		if not cls:
			# An existing cycle has no class yet. Read it from the flag it was
			cls = "Summer Flower" if cint(self.get("custom_is_summer_flower_cycle")) \
				else "Rose"
			self.custom_crop_class = cls
		self.custom_is_summer_flower_cycle = 1 if cls == "Summer Flower" else 0

	def fill_farm_from_block(self):
		"""A block cycle's farm comes from its block."""
		if self.get("farm") or not self.get("custom_block"):
			return
		farm = frappe.db.get_value("Block", self.custom_block, "farm")
		if farm:
			self.farm = farm

	# --------------------------------------------------------------- naming
	def autoname(self):
		"""Name summer flower cycles per block-planting."""
		if not self.get("custom_is_summer_flower_cycle"):
			return
		if self.get("custom_planting_calendar"):
			self.name = "CC-{0}".format(self.custom_planting_calendar)
			return
		# A block is replanted many times over, so the block alone is not unique;
		if self.get("custom_block") and self.get("custom_planting_date"):
			y, w = iso_year_week(getdate(self.custom_planting_date))
			self.name = "CC-{0}-{1}W{2:02d}".format(self.custom_block, y, w)

	# ------------------------------------------------------------- validate
	def validate(self):
		self.settle_scope()
		self.check_scope()
		super().validate()

		# Scope is what the cycle occupies; the summer flower flag is which planning
		if self.is_block_scoped():
			self.sync_block_geometry()
		else:
			# greenhouse is unique on this doctype, so only a block-scoped cycle may
			if not self.get("greenhouse"):
				frappe.throw(_("Greenhouse is required on a greenhouse-scoped cycle."))

		if not self.get("custom_is_summer_flower_cycle"):
			return

		self._version = self._resolve_version()
		if self._version is None:
			# Recorded, not planned. The block, the beds and the dates are facts
			self.set_plant_age()
			self.check_uproot_deviation()
			self.sync_cycle_status()
			return
		self.protocol = EffectiveProtocol(self._version, self)

		self.check_override_approval()
		self.set_dates()
		self.set_plant_age()
		self.check_uproot_deviation()
		self.build_flush_schedule()
		self.roll_up_harvest()
		self.build_weekly_targets()
		self.sync_cycle_status()
		self.compute_royalty()
		self.sync_biological_asset()

	# ------------------------------------------------------------------ scope
	def settle_scope(self):
		"""Work the scope out from the crop, then hold the record to it."""
		if not self.get("custom_cycle_scope"):
			self.custom_cycle_scope = ("Block" if self.summer_flower_crop()
			                           else "Greenhouse")
		# The flag and the scope answer different questions -- which planner, and
		if self.summer_flower_crop() and not self.get("custom_is_summer_flower_cycle"):
			self.custom_is_summer_flower_cycle = 1

	def summer_flower_crop(self):
		"""Whether this cycle's variety is planned as a summer flower."""
		if cint(self.get("custom_is_summer_flower_cycle")):
			return True
		variety = self.get("variety") or self.get("custom_sf_variety")
		if not variety:
			return False
		if self.get("custom_crop_protocol_version"):
			return True
		return bool(frappe.db.exists("Crop Protocol",
		                             {"variety": variety,
		                              "custom_is_summer_flower": 1}))

	def check_scope(self):
		"""One thing occupied, and the right one."""
		scope = self.get("custom_cycle_scope")
		block, house = self.get("custom_block"), self.get("greenhouse")
		if scope == "Block":
			if not block:
				frappe.throw(
					_("This is a block cycle, so it needs a Block. Summer flowers are "
					  "planned on blocks; roses and the bed-range crops are planned "
					  "on a greenhouse -- change the scope if that is what this is."),
					title=_("No block"))
			if house:
				frappe.throw(
					_("A block cycle is described by its block, not by a greenhouse. "
					  "{0} is set as well, which would put this crop in two places at "
					  "once. Clear it, or make this a greenhouse cycle.").format(house),
					title=_("Both a block and a greenhouse"))
		else:
			if not house:
				frappe.throw(
					_("This is a greenhouse cycle, so it needs a Greenhouse. If this "
					  "is a summer flower planted on a block, set the scope to Block."),
					title=_("No greenhouse"))
			if block:
				frappe.throw(
					_("A greenhouse cycle is described by a bed range on the "
					  "greenhouse, not by a block. {0} is set as well. Clear it, or "
					  "make this a block cycle.").format(block),
					title=_("Both a greenhouse and a block"))

	def is_block_scoped(self):
		"""Block-scoped when scope says so, or when a block is set."""
		scope = self.get("custom_cycle_scope")
		if scope:
			return scope == "Block"
		return bool(self.get("custom_block"))

	def _resolve_version(self):
		"""The protocol this cycle is planned against, where there is one."""
		if not self.get("custom_crop_protocol_version"):
			return None
		return frappe.get_cached_doc("Crop Protocol Version",
		                            self.custom_crop_protocol_version)

	# ------------------------------------------------------------- geometry
	def sync_block_geometry(self):
		"""Align a block-scoped cycle with the block it occupies."""
		if not self.get("custom_block"):
			frappe.throw(_("A block-scoped cycle needs a Block."))
		# A block cycle naming its beds is how the farm records a planting: the
		self.greenhouse = None
		house, farm = frappe.db.get_value("Block", self.custom_block,
		                                  ["greenhouse", "farm"])
		self.custom_greenhouse = house
		# Farm is read-only and fetched from greenhouse.custom_farm, which a block
		if farm:
			self.farm = farm
		elif house:
			self.farm = frappe.db.get_value("Warehouse", house, "custom_farm")
		ha = flt(frappe.db.get_value("Block", self.custom_block,
		                             "custom_net_area_ha"))
		area_sqm = ha * 10_000
		self.custom_block_area_sqm = area_sqm

		# The doctype names itself after its greenhouse, which a block cycle
		self.title = "{0} @ {1}".format(variety_name(
			self.variety or self.get("custom_sf_variety")), self.custom_block)
		# planted_area is summed from the bed rows, and a block cycle recorded by
		if not self.get("beds") and flt(self.get("custom_area_planted_sqm")):
			self.planted_area = flt(self.custom_area_planted_sqm)
			# And the figure derived from it. The agriculture controller works
			density = flt(self.get("custom_planting_density_per_sqm")) or flt(
				self.get("plants_per_sqm"))
			if density:
				self.implied_plants = int(round(self.planted_area * density))

		if not flt(self.custom_planting_density_per_sqm):
			# _version is resolved further down validate(), after the summer flower
			version = getattr(self, "_version", None)
			if version is None and self.get("custom_crop_protocol_version"):
				version = frappe.get_cached_doc(
					"Crop Protocol Version", self.custom_crop_protocol_version)
			if version is not None:
				self.custom_planting_density_per_sqm = flt(version.plants_per_sqm_net)

		if flt(self.custom_area_planted_sqm) and area_sqm and \
				flt(self.custom_area_planted_sqm) > area_sqm + 0.5:
			frappe.throw(_(
				"Planted area {0} m² exceeds block {1}'s net area of {2} m². "
				"A planting can fill the block but cannot exceed it."
			).format(flt(self.custom_area_planted_sqm), self.custom_block,
			         round(area_sqm, 2)))

		# Beds are what a grower records -- "we planted nineteen beds". Area is
		if not flt(self.custom_area_planted_sqm) and cint(self.custom_beds_planted):
			block = None
			if self.get("custom_block"):
				try:
					block = frappe.get_doc("Block", self.custom_block)
				except frappe.DoesNotExistError:
					block = None
			per_bed = self._per_bed_area(block) if block is not None else 0
			if per_bed > 0:
				self.custom_area_planted_sqm = round(
					cint(self.custom_beds_planted) * per_bed, 2)

		self.custom_area_utilisation_pct = (
			flt(self.custom_area_planted_sqm) * 100.0 / area_sqm if area_sqm else 0
		)
		if not cint(self.custom_live_plant_count) and flt(self.custom_area_planted_sqm):
			self.custom_live_plant_count = int(round(
				flt(self.custom_area_planted_sqm) *
				flt(self.custom_planting_density_per_sqm)))

	# ---------------------------------------------------------------- dates
	def on_update(self):
		"""The block is written after the cycle exists, not during validate."""
		parent = getattr(super(), "on_update", None)
		if callable(parent):
			parent()
		if self.get("custom_cycle_scope") == "Block" and \
				not self.flags.get("skip_block_ledger"):
			# A bulk reload saves hundreds of cycles onto the same block; each
			self.write_block_ledger()

	def _per_bed_area(self, block):
		"""How big one bed on this block is, in m2."""
		beds = cint(self.get("custom_beds_planted"))
		if beds <= 0:
			return 0.0
		# 1. What this planting actually covered, divided by the beds it covered.
		planted = flt(self.get("custom_area_planted_sqm"))
		if planted > 0:
			return planted / beds
		# 2. A bed the block already has. This is deliberately ahead of the
		for b in (block.get("individual_beds") or []):
			if flt(b.get("area_m2")) > 0:
				return flt(b.area_m2)
		# 3. The block's area over the beds it is cut into. Right only while
		capacity = cint(block.get("custom_total_beds"))
		area = flt(block.get("block_area")) or \
			flt(block.get("custom_net_area_ha")) * 10_000
		if area > 0 and capacity > 0:
			return area / capacity
		# 4. The average bed the farm actually has on record.
		from upande_summer_flowers.summer_flowers.crop_protocol import farm_bed_geometry
		avg, _beds = farm_bed_geometry(self.get("farm"))
		return flt(avg)

	def _is_standing(self):
		"""Is this cycle still holding ground today?"""
		if self.get("custom_sf_cycle_status") in ENDED_STATES:
			return False
		if self.get("custom_actual_uproot_date"):
			return getdate(self.custom_actual_uproot_date) > getdate(nowdate())
		uproot = self.get("custom_planned_uproot_date")
		if uproot:
			return getdate(uproot) > getdate(nowdate())
		return True

	def write_block_ledger(self):
		"""Put this cycle's planting onto the Block, as a greenhouse cycle does."""
		# A summer flower cycle can carry its variety in either field: the native
		variety = self.variety or self.get("custom_sf_variety")
		if not self.get("custom_block") or not variety:
			return
		beds = cint(self.get("custom_beds_planted"))
		if beds <= 0:
			return
		try:
			block = frappe.get_doc("Block", self.custom_block)
		except frappe.DoesNotExistError:
			return
		if not block.meta.has_field("bed_range"):
			return            # an older Block that has not been mirrored yet

		# One row per cycle, replaced rather than added to, so re-saving a cycle
		rows = [r for r in (block.get("bed_range") or [])
		        if (r.crop_cycle or "") != self.name]
		if not self._is_standing():
			# Uprooted or ended: the cycle keeps its own record, but it stops
			if len(rows) != len(block.get("bed_range") or []):
				block.set("bed_range", rows)
				block.flags.ignore_permissions = True
				block.save()
			return

		per_bed = self._per_bed_area(block)
		if per_bed <= 0:
			frappe.msgprint(
				_("Block {0} has no bed size on record, so cycle {1} could not be "
				  "put on its bed ledger. Set the block's area and total beds.")
				.format(self.custom_block, self.name),
				indicator="orange", alert=True)
			return
		# The block's length is the length of a bed running down it, which is how
		length = flt(block.get("block_length")) or round(per_bed ** 0.5, 4)
		width = round(per_bed / length, 4)

		# The cycle is what actually stands on the ground, so the block has to
		density = 0.0
		if cint(self.get("custom_live_plant_count")) and flt(self.get("custom_area_planted_sqm")):
			density = (cint(self.custom_live_plant_count)
			           / flt(self.custom_area_planted_sqm))
		density = density or flt(self.get("custom_planting_density_per_sqm")) or \
			flt(frappe.db.get_value("Crop Protocol Version",
			                        self.get("custom_crop_protocol_version"),
			                        "plants_per_sqm_net"))
		taken = max([cint(r.to_bed) for r in rows] or [0])
		block.set("bed_range", rows)
		block.append("bed_range", {
			"from_bed": taken + 1, "to_bed": taken + beds,
			"variety": variety,
			"crop_protocol": self.get("crop_protocol"),
			"planting_date": self.get("custom_planting_date") or self.planting_date,
			"bed_length": length,
			"bed_width": width,
			"plants_per_sqm": density,
			"crop_cycle": self.name,
		})
		block.flags.ignore_permissions = True
		block.save()

	def set_dates(self):
		"""Planned dates from the effective protocol; actuals override forward."""
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
		"""Age today, or age at uprooting once that has actually happened."""
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
		"""A cycle-level protocol override needs justification and sign-off."""
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
		plants = cint(self.custom_live_plant_count)
		# Two figures, because they answer two questions. What comes off the
		from upande_summer_flowers.summer_flowers.doctype \
			.summer_flower_production_plan.summer_flower_production_plan import saleable

		grades_out = saleable(self._version)

		self.set("custom_flush_schedule", [])
		for number, weeks_from_pinch, spp in self.protocol.flush_rows():
			harvest = add_days(anchor, 7 * weeks_from_pinch)
			if end and getdate(harvest) > end:
				break
			y, w = iso_year_week(getdate(harvest))
			no = len(self.get("custom_flush_schedule")) + 1
			harvested, actual = actuals.get(no, (0, 0))
			expected = int(round(spp * plants))
			self.append("custom_flush_schedule", {
				"flush_number": no, "harvest_date": harvest, "year": y,
				"week_no": w, "stems_per_plant": spp, "expected_stems": expected,
				"saleable_stems": int(round(expected * grades_out)),
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
		# The same life, net of what fails grading. Kept beside the gross rather
		self.custom_saleable_stems_life = sum(cint(r.saleable_stems) for r in rows)
		# Variance is against what the harvested flushes were expected to yield,
		self.custom_harvest_variance_stems = (
			self.custom_total_harvested_stems
			- sum(cint(r.expected_stems) for r in done)
		)
		self.custom_current_flush_number = cint(done[-1].flush_number) if done else 0
		self.custom_last_harvest_date = done[-1].harvest_date if done else None
		self.custom_flushes_remaining = len(todo)
		self.custom_expected_stems_remaining = sum(cint(r.expected_stems) for r in todo)
		self.custom_saleable_stems_remaining = sum(cint(r.saleable_stems) for r in todo)
		if todo:
			self.custom_next_harvest_date = todo[0].harvest_date
			self.custom_expected_stems_next_flush = cint(todo[0].expected_stems)
		else:
			self.custom_next_harvest_date = None
			self.custom_expected_stems_next_flush = 0

	# ------------------------------------------------------ weekly targets
	def build_weekly_targets(self):
		"""Expected stems per harvest week per grade."""
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

		# Actuals and hand-set targets survive the rebuild; auto-defaulted targets
		prev = {
			(cint(r.year), cint(r.week_no), r.grade): (
				cint(r.target_stems), cint(r.is_manual), cint(r.actual_stems), r.notes)
			for r in (self.get("custom_weekly_targets") or [])
		}
		self.set("custom_weekly_targets", [])
		for fl in (self.get("custom_flush_schedule") or []):
			for grade, pct in grades:
				# The share of what grades out, not of what is cut: a target is
				expected = int(round(cint(fl.saleable_stems) * pct / 100.0))
				key = (cint(fl.year), cint(fl.week_no), grade)
				old_target, manual, actual, notes = prev.get(key, (0, 0, 0, None))
				target = old_target if (manual and old_target) else expected
				self.append("custom_weekly_targets", {
					"year": fl.year, "week_no": fl.week_no,
					"week_start_date": _monday(cint(fl.year), cint(fl.week_no)),
					"flush_number": fl.flush_number, "grade": grade,
					"expected_stems": expected,
					"target_stems": target,
					"is_manual": manual,
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

	# ---------------------------------------------------- biological asset
	def sync_biological_asset(self):
		"""A standing crop is a biological asset, so make it a real Asset."""
		self.custom_biological_asset_value = (
			flt(self.custom_cost_per_plant) * cint(self.custom_live_plant_count))
		if not cint(self.get("custom_is_biological_asset")):
			return
		if not flt(self.custom_cost_per_plant):
			frappe.throw(_(
				"A biological asset needs a cost per plant to be worth anything."))
		if self.get("custom_biological_asset"):
			if frappe.db.get_value("Asset", self.custom_biological_asset,
			                       "docstatus") == 0:
				meta = frappe.get_meta("Asset")
				vals = {f: flt(self.custom_biological_asset_value)
				        for f in ASSET_VALUE_FIELDS if meta.has_field(f)}
				if meta.has_field("asset_quantity"):
					vals["asset_quantity"] = cint(self.custom_live_plant_count) or 1
				frappe.db.set_value("Asset", self.custom_biological_asset, vals,
				                    update_modified=False)
			return
		# Created on insert only once the cycle has what an Asset needs.
		if self.is_new() or not self.get("custom_planting_date"):
			return
		self.custom_biological_asset = create_biological_asset(self)

	# ------------------------------------------------------------- actions
	@frappe.whitelist()
	def create_asset(self):
		"""Create the Asset for a cycle already flagged as a biological asset."""
		if not cint(self.get("custom_is_biological_asset")):
			frappe.throw(_("Tick Is Biological Asset first."))
		if self.get("custom_biological_asset") and frappe.db.exists(
				"Asset", self.custom_biological_asset):
			return self.custom_biological_asset
		name = create_biological_asset(self)
		self.db_set("custom_biological_asset", name)
		return name

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
		if not _has_role(_settings.target_roles()):
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
		if not _has_role(_settings.target_roles()):
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
		when = getdate(harvest_date) if harvest_date else (
			getdate(row.harvest_date) if row.harvest_date else getdate(nowdate()))
		if when > getdate(nowdate()):
			frappe.throw(_(
				"Flush {0} would be dated {1}, which is in the future. Record the "
				"harvest on or after the day it was cut."
			).format(flush_number, when))
		if cint(actual_stems) < 0:
			frappe.throw(_("Harvested stems cannot be negative."))
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
		"""Cuttings taken off this cycle for propagation."""
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


def variety_name(variety):
	"""What the farm calls this variety, falling back to its item code."""
	if not variety:
		return variety
	try:
		return frappe.get_cached_value("Item", variety, "item_name") or variety
	except Exception:
		return variety


def _monday(year, week):
	try:
		return datetime.date.fromisocalendar(year, week, 1)
	except ValueError:
		return None


# ---------------------------------------------------------------------------

BIO_CATEGORY = "BIOLOGICAL ASSETS (PLANTS)"


def bio_asset_item(variety):
	"""The fixed-asset Item that stands for a variety as a biological asset."""
	nominated = frappe.db.get_single_value("Summer Flower Settings",
	                                       "biological_asset_item")
	if nominated:
		return nominated
	code = "BIO-{0}".format(variety)[:140]
	if frappe.db.exists("Item", code):
		return code
	if not frappe.db.exists("Asset Category", BIO_CATEGORY):
		frappe.throw(_(
			"Asset Category {0} does not exist, so a biological asset cannot be "
			"created.").format(BIO_CATEGORY))
	item = frappe.new_doc("Item")
	item.item_code = code
	item.item_name = "{0} (standing crop)".format(variety)[:140]
	item.item_group = frappe.db.get_value("Item", variety, "item_group") or \
		frappe.db.get_value("Item Group", {"is_group": 0}, "name")
	item.stock_uom = "Nos"
	item.is_fixed_asset = 1
	item.is_stock_item = 0
	# Set as an integer on purpose. Asset.validate_item reads this through
	item.disabled = 0
	item.asset_category = BIO_CATEGORY
	item.description = _(
		"Standing crop of {0} held as a biological asset. Not a stock item: the "
		"stems it produces are.").format(variety)
	item.flags.ignore_permissions = True
	item.flags.ignore_mandatory = True
	item.insert()
	frappe.clear_document_cache("Item", item.name)
	return item.name


def _check_item_usable(code):
	"""This site auto-disables any Item that is not through item approval."""
	row = frappe.db.get_value("Item", code,
	                          ["disabled", "workflow_state"], as_dict=True)
	if not row:
		frappe.throw(_("Item {0} does not exist.").format(code))
	if row.disabled:
		frappe.throw(_(
			"Item {0} exists but is disabled: this site disables any item that has "
			"not been through item approval, and it is currently {1}. Get the item "
			"approved, or nominate an approved fixed-asset item in Summer Flower "
			"Settings, then create the asset again."
		).format(code, row.workflow_state or _("Draft")))
	return code


def _asset_location(farm, greenhouse=None):
	"""Asset needs a Location. Prefer the farm's own, then its name."""
	if farm:
		# custom_location is another app's field on Farm, and a site can be without
		loc = (frappe.db.get_value("Farm", farm, "custom_location")
		       if frappe.get_meta("Farm").has_field("custom_location") else None)
		if loc and frappe.db.exists("Location", loc):
			return loc
		if frappe.db.exists("Location", farm):
			return farm
	if greenhouse:
		# Greenhouse names carry the site, e.g. "Simotwo GH 17 - KR".
		head = str(greenhouse).split(" ")[0]
		if frappe.db.exists("Location", head):
			return head
	return None


# Asset renamed its value fields across versions -- this one has purchase_amount and
ASSET_VALUE_FIELDS = ("gross_purchase_amount", "purchase_amount",
                      "net_purchase_amount")


def _set_asset_value(asset, value, quantity):
	meta = frappe.get_meta("Asset")
	wrote = []
	for f in ASSET_VALUE_FIELDS:
		if meta.has_field(f):
			asset.set(f, value)
			wrote.append(f)
	if not wrote:
		frappe.throw(_("Asset has no purchase-amount field this version recognises."))
	if meta.has_field("asset_quantity"):
		asset.asset_quantity = quantity
	return wrote


def create_biological_asset(cycle):
	"""Create a non-depreciating Asset for a standing crop."""
	variety = cycle.get("custom_sf_variety") or cycle.get("variety")
	if not variety:
		frappe.throw(_("A biological asset needs a variety."))
	farm = cycle.get("farm")
	location = _asset_location(farm, cycle.get("custom_greenhouse")
	                           or cycle.get("greenhouse"))
	if not location:
		frappe.throw(_(
			"Asset requires a Location and none matches farm {0}. Set the farm's "
			"Location, or create a Location named after it."
		).format(farm))
	company = cycle.get("company") or frappe.db.get_value("Farm", farm, "company")
	where = cycle.get("custom_block") or cycle.get("greenhouse") or farm

	asset = frappe.new_doc("Asset")
	asset.item_code = _check_item_usable(bio_asset_item(variety))
	asset.asset_name = "{0} - {1} - planted {2}".format(
		variety, str(where).split(" - ").pop(), cycle.get("custom_planting_date"))[:140]
	asset.asset_category = BIO_CATEGORY
	asset.company = company
	asset.location = location
	asset.purchase_date = getdate(cycle.get("custom_planting_date"))
	# It starts earning at first harvest, not at planting.
	first = None
	for r in (cycle.get("custom_flush_schedule") or []):
		first = r.harvest_date
		break
	asset.available_for_use_date = getdate(first or cycle.get("custom_planting_date"))
	_set_asset_value(asset, flt(cycle.get("custom_biological_asset_value")),
	                 cint(cycle.get("custom_live_plant_count")) or 1)
	asset.calculate_depreciation = 0
	asset.is_existing_asset = 1
	asset.flags.ignore_permissions = True
	asset.flags.ignore_mandatory = True
	asset.insert()
	frappe.msgprint(
		_("Biological asset {0} created as a draft for finance to submit.").format(
			frappe.utils.get_link_to_form("Asset", asset.name)),
		indicator="green", alert=True)
	return asset.name
