# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Splitting a block into several, or recombining several into one.

A block is a piece of land, so the land does not appear or vanish when the fences
move: the resulting gross area must equal the source gross area, and the request
will not execute if it does not. Both directions need Farm Manager approval and a
justification, and neither is allowed while a planting is standing on the land --
a plant cannot be transferred between blocks by editing a record.
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime

APPROVER_ROLES = ("Farm Manager", "Agriculture Manager", "System Manager")
AREA_TOLERANCE_HA = 0.0005  # half a square metre, to absorb rounding


def _has_role(roles=APPROVER_ROLES):
	return bool(set(roles) & set(frappe.get_roles(frappe.session.user)))


class BlockChangeRequest(Document):
	def validate(self):
		self.pull_sources()
		self.check_shape()
		self.check_area_conserved()
		self.check_nothing_standing()

	def pull_sources(self):
		for r in self.sources:
			info = frappe.db.get_value(
				"Block", r.block,
				["custom_net_area_ha", "custom_total_beds", "farm"], as_dict=True)
			if not info:
				continue
			r.net_area_ha = flt(info.custom_net_area_ha)
			r.total_beds = int(info.custom_total_beds or 0)
			r.standing_plantings = self._standing_count(r.block)
		self.source_area_ha = sum(flt(r.net_area_ha) for r in self.sources)
		self.result_area_ha = sum(flt(r.net_area_ha) for r in self.results)
		self.area_difference_ha = flt(self.result_area_ha) - flt(self.source_area_ha)

	@staticmethod
	def _standing_count(block):
		from upande_summer_flowers.summer_flowers.doctype.planting_calendar.planting_calendar import (
			STANDING_STATES,
		)
		return frappe.db.count("Planting Calendar", {
			"block": block, "calendar_status": ["in", STANDING_STATES]})

	def check_shape(self):
		if not self.sources:
			frappe.throw(_("Name at least one source block."))
		if not self.results:
			frappe.throw(_("Name the resulting blocks."))
		if self.request_type == "Split":
			if len(self.sources) != 1:
				frappe.throw(_("A split starts from exactly one block."))
			if len(self.results) < 2:
				frappe.throw(_("A split has to produce at least two blocks."))
		else:
			if len(self.sources) < 2:
				frappe.throw(_("A recombine needs at least two source blocks."))
			if len(self.results) != 1:
				frappe.throw(_("A recombine produces exactly one block."))
		seen = set()
		for r in self.sources:
			if r.block in seen:
				frappe.throw(_("Block {0} is listed twice as a source.").format(r.block))
			seen.add(r.block)
		codes = set()
		for r in self.results:
			if r.block_code in codes:
				frappe.throw(_("Resulting block code {0} is used twice.")
				             .format(r.block_code))
			codes.add(r.block_code)

	def check_area_conserved(self):
		"""Land is conserved. Moving a fence does not create or destroy area."""
		if abs(flt(self.area_difference_ha)) > AREA_TOLERANCE_HA:
			frappe.throw(_(
				"The resulting blocks total {0} ha but the source blocks total {1} ha, "
				"a difference of {2} ha. A {3} rearranges land, it does not create or "
				"lose it -- correct the areas, or record an area revision on the "
				"source block first if the land itself has changed."
			).format(round(flt(self.result_area_ha), 4),
			         round(flt(self.source_area_ha), 4),
			         round(flt(self.area_difference_ha), 4),
			         (self.request_type or "change").lower()))

	def check_nothing_standing(self):
		"""A standing planting pins the land it is on."""
		busy = [(r.block, r.standing_plantings) for r in self.sources
		        if int(r.standing_plantings or 0)]
		if busy:
			frappe.throw(_(
				"{0} still has a standing planting. Uproot or end the cycle before "
				"rearranging the block -- plants cannot be moved between blocks by "
				"editing a record."
			).format(", ".join("%s (%d)" % b for b in busy)))

	# ------------------------------------------------------------- actions
	@frappe.whitelist()
	def submit_for_approval(self):
		if not (self.justification or "").strip():
			frappe.throw(_("A justification is required."))
		self.status = "Pending Approval"
		self.rejection_reason = None
		self.save()
		return self.status

	@frappe.whitelist()
	def approve(self):
		if not _has_role():
			frappe.throw(_("Only a Farm Manager can approve a block change."))
		if self.status != "Pending Approval":
			frappe.throw(_("Submit the request for approval first."))
		if not (self.justification or "").strip():
			frappe.throw(_("A justification is required."))
		self.status = "Approved"
		self.approved_by = frappe.session.user
		self.approved_on = now_datetime()
		self.save()
		return self.status

	@frappe.whitelist()
	def reject(self, reason=None):
		if not _has_role():
			frappe.throw(_("Only a Farm Manager can reject a block change."))
		if not (reason or "").strip():
			frappe.throw(_("A rejection reason is required."))
		self.status = "Rejected"
		self.rejection_reason = reason
		self.save()
		return self.status

	@frappe.whitelist()
	def execute(self):
		"""Create or resize the resulting blocks and retire the sources.

		Sources are retired rather than deleted: their history -- past plantings,
		past area revisions -- is the record of what happened on that land and
		has to survive the rearrangement.
		"""
		if self.status != "Approved":
			frappe.throw(_("A block change must be approved before it is executed."))
		if self.status == "Executed":
			return

		created = []
		for r in self.results:
			if r.existing_block:
				blk = frappe.get_doc("Block", r.existing_block)
			else:
				blk = frappe.new_doc("Block")
				# Block's own naming already prefixes "Block ", so a code entered
				# as "Block 11A-1" would come out as "... Block Block 11A-1".
				blk.block = re.sub(r"^\s*block\s+", "", r.block_code or "",
				                   flags=re.I).strip()
				blk.farm = self.farm
				src = frappe.get_doc("Block", self.sources[0].block)
				blk.greenhouse = src.greenhouse
				blk.custom_is_summer_flower_block = 1
			blk.custom_is_summer_flower_block = 1
			blk.append("custom_area_history", {
				"effective_from": getdate(self.effective_date),
				"net_area_ha": flt(r.net_area_ha),
				"total_beds": int(r.total_beds or 0) or None,
				"reason": _("{0} via {1}: {2}").format(
					self.request_type, self.name, self.justification)[:140],
				"approved_by": self.approved_by,
			})
			blk.flags.ignore_permissions = True
			blk.save()
			r.created_block = blk.name
			created.append(blk.name)

			if r.bed_from and r.bed_to:
				self._move_beds(r.bed_from, r.bed_to, blk.name)

		for r in self.sources:
			if r.block in created:
				continue
			# Retired, not deleted: the land's history stays readable.
			frappe.db.set_value("Block", r.block,
			                    "custom_is_summer_flower_block", 0,
			                    update_modified=False)

		self.status = "Executed"
		self.executed_on = now_datetime()
		self.save()
		frappe.db.commit()
		return {"created": created, "retired": [
			r.block for r in self.sources if r.block not in created]}

	def _move_beds(self, bed_from, bed_to, block):
		beds = frappe.get_all("Bed", filters={
			"custom_block": ["in", [r.block for r in self.sources]],
			"bed": ["between", [int(bed_from), int(bed_to)]],
		}, pluck="name")
		for b in beds:
			frappe.db.set_value("Bed", b, "custom_block", block,
			                    update_modified=False)
		return len(beds)
