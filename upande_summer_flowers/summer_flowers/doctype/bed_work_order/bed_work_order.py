# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Uprooting or replanting a run of beds, over as many days as it takes.

The native Replanting Log and Uprooting Log record a whole bed range against a
single date, so there was no way to say that two and a half beds were done today
and the rest would follow tomorrow. This is the job; the logs still get their
summary row when it finishes, so the cycle's own tabs remain the record of what
happened.

Progress is measured in beds and is deliberately fractional. Half a bed of work
is half a bed of plants, and the cycle's live plant count moves with it, because
a forecast built on plants that are already out of the ground is wrong.
"""

import math

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate, nowdate


class BedWorkOrder(Document):
	def validate(self):
		self.pull_context()
		self.check_range()
		self.roll_up_progress()
		self.sync_status()

	def on_update(self):
		self.apply_to_cycle()

	def on_trash(self):
		# Undo whatever this job had already applied, or the cycle keeps a plant
		# count that reflects work now deleted.
		if cint(self.plants_applied):
			self._shift_cycle_plants(-cint(self.plants_applied))

	# ------------------------------------------------------------- context
	def pull_context(self):
		cyc = frappe.db.get_value(
			"Crop Cycle", self.crop_cycle,
			["custom_block", "custom_greenhouse", "greenhouse", "farm", "company",
			 "custom_sf_variety", "custom_live_plant_count", "custom_beds_planted",
			 "custom_cycle_scope", "number_of_beds"], as_dict=True)
		if not cyc:
			frappe.throw(_("Crop cycle {0} does not exist.").format(self.crop_cycle))
		self.block = cyc.custom_block
		self.greenhouse = cyc.custom_greenhouse or cyc.greenhouse
		self.farm = cyc.farm
		self.company = cyc.company
		self.variety = cyc.custom_sf_variety
		if not flt(self.plants_per_bed):
			beds = cint(cyc.custom_beds_planted) or cint(cyc.number_of_beds)
			if beds:
				self.plants_per_bed = flt(cyc.custom_live_plant_count) / beds

	def check_range(self):
		if cint(self.to_bed) < cint(self.from_bed):
			frappe.throw(_("The bed range ends before it starts."))
		self.total_beds = cint(self.to_bed) - cint(self.from_bed) + 1
		if self.block:
			# Bed numbers are unique within a greenhouse, not within a block, so a
			# block's beds are an arbitrary range -- 201-240, not 1-40. Checking the
			# range against the bed count would reject every block but the first.
			rng = frappe.db.sql("""select min(bed) lo, max(bed) hi, count(*) n
				from tabBed where custom_block = %s""", (self.block,), as_dict=True)[0]
			if not rng["n"]:
				frappe.throw(_("Block {0} has no bed records.").format(self.block))
			if cint(self.from_bed) < cint(rng["lo"]) or cint(self.to_bed) > cint(rng["hi"]):
				frappe.throw(_(
					"Block {0} runs from bed {1} to bed {2}, so {3}-{4} is outside it."
				).format(self.block, rng["lo"], rng["hi"], self.from_bed, self.to_bed))
		# Two live jobs over the same beds would each move the plant count.
		clash = frappe.db.sql("""
			select name from `tabBed Work Order`
			where crop_cycle = %(cycle)s and name != %(name)s
			  and status not in ('Completed', 'Cancelled')
			  and from_bed <= %(to_bed)s and to_bed >= %(from_bed)s
			limit 1
		""", {"cycle": self.crop_cycle, "name": self.name or "",
		      "from_bed": self.from_bed, "to_bed": self.to_bed})
		if clash:
			frappe.throw(_(
				"{0} is already working beds {1}-{2} of this cycle. Finish or cancel "
				"it before opening another over the same beds."
			).format(clash[0][0], self.from_bed, self.to_bed))

	# ------------------------------------------------------------ progress
	def roll_up_progress(self):
		"""Running total, and exactly which bed the work is standing in."""
		rows = sorted(self.progress, key=lambda r: (getdate(r.work_date), r.idx))
		per_bed = flt(self.plants_per_bed)
		cumulative = 0.0
		for r in rows:
			if flt(r.beds_done) <= 0:
				frappe.throw(_("A progress entry has to record some work."))
			cumulative += flt(r.beds_done)
			if cumulative > flt(self.total_beds) + 0.0001:
				frappe.throw(_(
					"Progress adds up to {0} beds but the job is only {1} beds. Check "
					"the entry dated {2}."
				).format(round(cumulative, 2), flt(self.total_beds), r.work_date))
			r.cumulative_beds = round(cumulative, 4)
			whole = math.floor(cumulative + 1e-9)
			frac = cumulative - whole
			# The bed being worked is the one after the last finished bed. When the
			# total lands exactly on a boundary there is no bed in progress.
			r.bed_reached = cint(self.from_bed) + whole - (1 if frac < 1e-9 else 0)
			r.fraction_in_bed = round(frac * 100, 2)
			r.plants = int(round(flt(r.beds_done) * per_bed))
			if not r.recorded_by:
				r.recorded_by = frappe.session.user

		self.beds_done = round(cumulative, 4)
		self.beds_remaining = round(flt(self.total_beds) - cumulative, 4)
		self.pct_complete = (cumulative * 100.0 / flt(self.total_beds)
		                     if flt(self.total_beds) else 0)
		whole = math.floor(cumulative + 1e-9)
		frac = cumulative - whole
		self.last_full_bed = (cint(self.from_bed) + whole - 1) if whole else 0
		self.current_bed = (cint(self.from_bed) + whole) if frac > 1e-9 else 0
		self.fraction_in_bed = round(frac * 100, 2)
		self.plants_affected = int(round(cumulative * per_bed))
		self.total_labour_hours = sum(flt(r.labour_hours) for r in rows)
		self.days_worked = len({str(getdate(r.work_date)) for r in rows})
		if rows:
			self.started_on = getdate(rows[0].work_date)
			self.last_worked_on = getdate(rows[-1].work_date)
		else:
			self.started_on = self.last_worked_on = None

	def sync_status(self):
		if self.status == "Cancelled":
			return
		done, total = flt(self.beds_done), flt(self.total_beds)
		if done <= 0:
			self.status = "Not Started"
			self.completed_on = None
		elif done + 0.0001 >= total:
			self.status = "Completed"
			self.completed_on = self.completed_on or self.last_worked_on or getdate(nowdate())
		else:
			# Paused is a person's judgement, so it is not overwritten here.
			if self.status != "Paused":
				self.status = "In Progress"
			self.completed_on = None

	# -------------------------------------------------------------- effects
	def apply_to_cycle(self):
		"""Move the cycle's live plant count by whatever changed since last save.

		A delta rather than a recompute, so re-saving or editing an entry cannot
		double-count, and so several jobs on one cycle each contribute once.
		"""
		want = cint(self.plants_affected)
		if self.work_type == "Uprooting":
			want = -want
		delta = want - cint(self.plants_applied)
		if delta:
			self._shift_cycle_plants(delta)
		self.db_set("plants_applied", want, update_modified=False)
		self.mark_beds()
		if self.status == "Completed" and not cint(self.logged_to_cycle):
			self.write_cycle_log()

	def _shift_cycle_plants(self, delta):
		cyc = frappe.get_doc("Crop Cycle", self.crop_cycle)
		new = max(0, cint(cyc.custom_live_plant_count) + cint(delta))
		cyc.custom_live_plant_count = new
		cyc.flags.ignore_permissions = True
		# Saving re-derives the flush schedule from the new plant count, which is the
		# whole point: stems still to come should reflect plants still in the ground.
		cyc.save()

	def mark_beds(self):
		"""Only finished beds change status. A half-worked bed is not done."""
		if not self.block or not cint(self.last_full_bed):
			return
		status = "Uprooted" if self.work_type == "Uprooting" else "Planted"
		beds = frappe.get_all("Bed", filters={
			"custom_block": self.block,
			"bed": ["between", [cint(self.from_bed), cint(self.last_full_bed)]],
		}, pluck="name")
		for b in beds:
			vals = {"custom_bed_status": status}
			if self.work_type == "Uprooting":
				vals["custom_uproot_date"] = self.last_worked_on
			frappe.db.set_value("Bed", b, vals, update_modified=False)

	def write_cycle_log(self):
		"""Put the summary row on the cycle's own replanting or uprooting tab."""
		cyc = frappe.get_doc("Crop Cycle", self.crop_cycle)
		if self.work_type == "Uprooting":
			cyc.append("uprooting_logs", {
				"uproot_date": self.completed_on or self.last_worked_on,
				"from_bed": cint(self.from_bed), "to_bed": cint(self.to_bed),
				"reason": self.reason or "Other",
				"qty_uprooted": cint(self.plants_affected),
				"remarks": _("{0}: {1} beds over {2} day(s), {3} labour hours.").format(
					self.name, flt(self.beds_done), cint(self.days_worked),
					flt(self.total_labour_hours)),
			})
		else:
			cyc.append("replanting_logs", {
				"replant_date": self.completed_on or self.last_worked_on,
				"from_bed": cint(self.from_bed), "to_bed": cint(self.to_bed),
				"qty_replanted": cint(self.plants_affected),
				"new_variety": self.new_variety,
				"cost_of_replanting": flt(self.cost),
				"remarks": _("{0}: {1} beds over {2} day(s), {3} labour hours.").format(
					self.name, flt(self.beds_done), cint(self.days_worked),
					flt(self.total_labour_hours)),
			})
			cyc.last_replanting_date = self.completed_on or self.last_worked_on
		cyc.flags.ignore_permissions = True
		cyc.save()
		self.db_set("logged_to_cycle", 1, update_modified=False)

	# -------------------------------------------------------------- actions
	@frappe.whitelist()
	def log_progress(self, beds_done, work_date=None, workers=None,
	                 labour_hours=None, notes=None):
		"""Record a day's work and say where that leaves the job."""
		if flt(beds_done) <= 0:
			frappe.throw(_("Record how many beds were done."))
		remaining = flt(self.total_beds) - flt(self.beds_done)
		if flt(beds_done) > remaining + 0.0001:
			frappe.throw(_(
				"Only {0} beds are left on this job, so {1} cannot have been done."
			).format(round(remaining, 2), flt(beds_done)))
		self.append("progress", {
			"work_date": getdate(work_date or nowdate()),
			"beds_done": flt(beds_done), "workers": cint(workers),
			"labour_hours": flt(labour_hours), "notes": notes,
			"recorded_by": frappe.session.user,
		})
		self.save()
		return self.where_it_reached()

	@frappe.whitelist()
	def where_it_reached(self):
		"""Plain answer to 'where did the work get to'."""
		if not flt(self.beds_done):
			return {"status": self.status, "summary": _("Not started."),
			        "beds_done": 0, "beds_remaining": flt(self.total_beds),
			        "pct_complete": 0}
		if self.status == "Completed":
			summary = _("Finished: all {0} beds ({1}-{2}) done.").format(
				flt(self.total_beds), self.from_bed, self.to_bed)
		elif cint(self.current_bed):
			summary = _(
				"Beds {0}-{1} finished, and {2}% of bed {3}. {4} of {5} beds to go."
			).format(self.from_bed, self.last_full_bed or self.from_bed,
			         flt(self.fraction_in_bed), self.current_bed,
			         flt(self.beds_remaining), flt(self.total_beds))
		else:
			summary = _("Beds {0}-{1} finished. {2} of {3} beds to go.").format(
				self.from_bed, self.last_full_bed, flt(self.beds_remaining),
				flt(self.total_beds))
		return {
			"status": self.status, "summary": summary,
			"beds_done": flt(self.beds_done),
			"beds_remaining": flt(self.beds_remaining),
			"pct_complete": flt(self.pct_complete),
			"last_full_bed": cint(self.last_full_bed),
			"current_bed": cint(self.current_bed),
			"fraction_in_bed": flt(self.fraction_in_bed),
			"plants_affected": cint(self.plants_affected),
			"days_worked": cint(self.days_worked),
			"logged_to_cycle": cint(self.logged_to_cycle),
		}

	@frappe.whitelist()
	def pause(self, reason=None):
		if self.status not in ("In Progress", "Not Started"):
			frappe.throw(_("Only a job in progress can be paused."))
		self.status = "Paused"
		if reason:
			self.add_comment("Info", _("Paused: {0}").format(reason))
		self.save()
		return self.status

	@frappe.whitelist()
	def resume(self):
		if self.status != "Paused":
			frappe.throw(_("This job is not paused."))
		self.status = "In Progress" if flt(self.beds_done) else "Not Started"
		self.save()
		return self.status

	@frappe.whitelist()
	def cancel_job(self, reason=None):
		"""Stop the job and hand back the plants it had taken off the cycle."""
		if not (reason or "").strip():
			frappe.throw(_("A reason is required to cancel a job."))
		if cint(self.plants_applied):
			self._shift_cycle_plants(-cint(self.plants_applied))
			self.db_set("plants_applied", 0, update_modified=False)
		self.status = "Cancelled"
		self.add_comment("Info", _("Cancelled: {0}").format(reason))
		self.save()
		return self.status
