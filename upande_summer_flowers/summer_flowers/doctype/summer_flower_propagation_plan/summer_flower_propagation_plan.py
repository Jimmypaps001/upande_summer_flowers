# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""How a Production Plan's cuttings get sourced.

The Production Plan says how many plants go in the ground in which week. The
Motherstock Batch already knows how to turn a peak weekly cutting requirement
into a TC order, a multiplication schedule and a cost. This is the layer between
them: the week-by-week requirement, what existing motherstock can already cover,
and therefore what actually has to be bought.

Sizing is on the peak week, not the annual total, because a cutting cannot be
banked -- it is stuck the week it is taken. A pool big enough for the year but
not for its busiest week fails in that week.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt, getdate, now_datetime

from upande_summer_flowers.summer_flowers.planning import iso_monday

APPROVER_ROLES = ("Farm Manager", "Agriculture Manager", "System Manager")


def _has_role(roles=APPROVER_ROLES):
	return bool(set(roles) & set(frappe.get_roles(frappe.session.user)))


class SummerFlowerPropagationPlan(Document):
	def validate(self):
		self.pull_header()
		self.note_protocol_provenance()
		self.build_requirement()
		self.apply_existing_motherstock()
		self.size_new_motherstock()
		self.roll_up()

	# --------------------------------------------------------------- header
	def pull_header(self):
		p = frappe.get_cached_doc("Summer Flower Production Plan",
		                          self.production_plan)
		self.market_demand = p.market_demand
		self.variety = p.variety
		self.farm = p.farm
		self.protocol = p.protocol
		self.company = p.company
		self.currency = p.currency
		self._plan = p
		self._version = frappe.get_cached_doc("Crop Protocol Version", p.protocol)

	def note_protocol_provenance(self):
		"""Every header field here is the production plan's answer, not a choice.

		The protocol decides cuttings per plant, the multiplication rate and the TC
		lead time, so a propagation plan built on a different version than its
		production plan would size the motherstock for a crop nobody is planting.
		It is fetched, never set -- and if the plan itself reports the version moved
		under it, that is said here rather than buried on the other document.
		"""
		p = self._plan
		v = self._version
		notes = [_("Protocol {0} (v{1}, {2}) comes from production plan {3}.")
		         .format(v.name, v.version, v.version_status, p.name)]
		if cint(p.get("protocol_stale")):
			notes.append("")
			notes.append(_("That plan reports its protocol changed since it was "
			               "built, so these numbers inherit the same gap:"))
			notes.append(p.protocol_note or "")
		self.protocol_note = "\n".join(x for x in notes if x is not None)

	# ---------------------------------------------------------- requirement
	def build_requirement(self):
		"""Plants to stick per week, grossed up into cuttings.

		Taken from the plan's proposed plantings, which already carry the sticking
		week the protocol implies, so the two documents cannot disagree about when
		a planting has to be started.
		"""
		v = self._version
		per_plant = flt(v.cuttings_per_plant_required) or 1.0
		self.cuttings_per_plant = per_plant

		by_week = {}
		for b in self._plan.plan_blocks:
			if not cint(b.is_new_planting):
				continue
			# A planting with no block will not happen, so raising cuttings for it
			# would size the motherstock against work that cannot be done.
			if cint(b.get("not_placed")):
				continue
			y, w = cint(b.sticking_year), cint(b.sticking_week)
			if not (y and w):
				continue
			slot = by_week.setdefault((y, w), {"plants": 0, "out": set()})
			slot["plants"] += cint(b.plants)
			if b.planting_year and b.planting_week:
				slot["out"].add("%s-W%02d" % (b.planting_year, cint(b.planting_week)))

		self.set("weeks", [])
		for (y, w) in sorted(by_week):
			slot = by_week[(y, w)]
			self.append("weeks", {
				"year": y, "week_no": w, "week_start_date": iso_monday(y, w),
				"plants_to_stick": slot["plants"],
				"cuttings_required": int(round(slot["plants"] * per_plant)),
				"plant_week": ", ".join(sorted(slot["out"]))[:140],
			})

	# ------------------------------------------------- existing motherstock
	def apply_existing_motherstock(self):
		"""Net off what standing motherstock can already cut in each week.

		A batch only counts between its first sticking date and its expiry: a pool
		that has not established yet, or is past its renewal, cannot supply the
		week being planned.
		"""
		v = self._version
		per_week = flt(v.cuttings_per_plant_per_week) or 1.0
		batches = frappe.get_all(
			"Summer Flower Motherstock Batch",
			filters={"variety": self.variety, "farm": self.farm,
			         "docstatus": ["<", 2]},
			fields=["name", "mother_plants", "first_sticking_date", "expiry_date",
			        "batch_status"])
		self.set("sources", [])
		pools = []
		for b in batches:
			if not (b.mother_plants and b.first_sticking_date):
				continue
			cap = int(round(cint(b.mother_plants) * per_week))
			pools.append({
				"cap": cap, "from": getdate(b.first_sticking_date),
				"to": getdate(b.expiry_date) if b.expiry_date else None,
			})
			self.append("sources", {
				"source_type": "Existing Motherstock",
				"motherstock_batch": b.name,
				"mother_plants": cint(b.mother_plants),
				"weekly_capacity": cap,
				"available_from": b.first_sticking_date,
				"available_to": b.expiry_date,
				"notes": _("Status {0}").format(b.batch_status),
			})

		for r in self.weeks:
			monday = getdate(r.week_start_date)
			available = 0
			for p in pools:
				if monday < p["from"]:
					continue
				if p["to"] and monday > p["to"]:
					continue
				available += p["cap"]
			covered = min(cint(r.cuttings_required), available)
			r.from_existing_ms = covered
			r.shortfall = cint(r.cuttings_required) - covered
			r.from_new_ms = 0

	# ------------------------------------------------------ new motherstock
	def size_new_motherstock(self):
		"""Size one new pool against the worst uncovered week.

		Sized on the peak shortfall rather than the total, for the same reason the
		whole module sizes motherstock on a peak: the pool has to supply the busiest
		week, and the weeks either side do not lend it capacity.
		"""
		v = self._version
		self._peak_shortfall = 0
		self._first_needed = None
		short = [r for r in self.weeks if cint(r.shortfall) > 0]
		if not short:
			self.mother_plants_required = 0
			self.peak_bench_sqm = 0
			return

		peak = max(cint(r.shortfall) for r in short)
		first_needed = min(getdate(r.week_start_date) for r in short)
		per_week = flt(v.cuttings_per_plant_per_week) or 1.0
		mothers = int(round(peak / per_week)) if per_week else 0
		self.mother_plants_required = mothers
		self.peak_bench_sqm = round(mothers / flt(v.plants_per_sqm_bench), 1) \
			if flt(v.plants_per_sqm_bench) else 0

		for r in self.weeks:
			if cint(r.shortfall) <= 0:
				continue
			if getdate(r.week_start_date) >= first_needed:
				take = min(cint(r.shortfall), peak)
				r.from_new_ms = take
				r.shortfall = cint(r.shortfall) - take

		self.append("sources", {
			"source_type": "New Motherstock (TC)",
			"mother_plants": mothers,
			"weekly_capacity": peak,
			"available_from": first_needed,
			"notes": _("Sized on the worst uncovered week, {0} cuttings.").format(peak),
		})
		self._peak_shortfall = peak
		self._first_needed = first_needed

	# --------------------------------------------------------------- totals
	def roll_up(self):
		self.total_plants_to_stick = sum(cint(r.plants_to_stick) for r in self.weeks)
		self.total_cuttings_required = sum(cint(r.cuttings_required) for r in self.weeks)
		self.cuttings_from_existing = sum(cint(r.from_existing_ms) for r in self.weeks)
		self.cuttings_from_new = sum(cint(r.from_new_ms) for r in self.weeks)
		self.cuttings_uncovered = sum(cint(r.shortfall) for r in self.weeks)
		self.weeks_sticking = len(self.weeks)
		self.existing_cover_pct = (
			self.cuttings_from_existing * 100.0 / self.total_cuttings_required
			if self.total_cuttings_required else 0)
		peak_row = max(self.weeks, key=lambda r: cint(r.cuttings_required),
		               default=None)
		self.peak_weekly_cuttings = cint(peak_row.cuttings_required) if peak_row else 0
		self.peak_week = ("%s-W%02d" % (peak_row.year, cint(peak_row.week_no))
		                  if peak_row else None)

		# The TC order is sized by running an unsaved Motherstock Batch through its
		# own controller, so these numbers and the real batch's cannot diverge.
		if cint(self.mother_plants_required) and self._first_needed:
			probe = self._probe_batch()
			self.tc_plants_required = cint(probe.tc_plants_required)
			self.tc_order_date = probe.tc_order_date
			self.tc_on_farm_date = probe.tc_on_farm_date
			self.first_sticking_date = probe.first_sticking_date
			self.tc_cost = flt(probe.tc_cost)
			self.total_cost = flt(probe.total_cost)
		else:
			self.tc_plants_required = 0
			self.tc_cost = 0
			self.total_cost = 0
			self.tc_order_date = None
			self.tc_on_farm_date = None
			self.first_sticking_date = None
		self.check_schedule()

	def check_schedule(self):
		"""Say plainly when the plan asks for cuttings that can no longer be grown.

		A production plan can propose a planting whose sticking week has already
		passed; the lead time back from that -- multiplication, establishment, lab
		turnaround -- then lands the TC order in the past. That is not a rounding
		nicety, it is weeks of production that cannot happen, so it is stated rather
		than left for someone to notice in the dates.
		"""
		today = getdate()
		notes = []
		late = [r for r in self.weeks
		        if getdate(r.week_start_date) < today and cint(r.plants_to_stick)]
		if late:
			notes.append(_(
				"{0} of {1} sticking weeks are already in the past, the earliest "
				"being {2}. Those cuttings cannot now be taken, so the plantings "
				"they feed will be late."
			).format(len(late), len(self.weeks), late[0].week_start_date))
		if self.tc_order_date and getdate(self.tc_order_date) < today:
			notes.append(_(
				"The TC order would have had to be placed on {0}, which is {1} days "
				"ago. Order now and first sticking moves to about {2}."
			).format(self.tc_order_date, (today - getdate(self.tc_order_date)).days,
			         self._earliest_feasible_sticking()))
		if cint(self.cuttings_uncovered):
			notes.append(_("{0} cuttings are not covered by any source.").format(
				cint(self.cuttings_uncovered)))
		self.schedule_warning = "\n".join(notes) or None

	def _earliest_feasible_sticking(self):
		"""First sticking date achievable if the TC were ordered today."""
		if not (self.tc_order_date and self.first_sticking_date):
			return None
		lead = (getdate(self.first_sticking_date) - getdate(self.tc_order_date)).days
		return add_days(getdate(), lead)

	def _probe_batch(self):
		b = frappe.new_doc("Summer Flower Motherstock Batch")
		b.variety = self.variety
		b.farm = self.farm
		b.protocol = self.protocol
		b.company = self.company
		b.peak_weekly_cuttings = cint(self._peak_shortfall)
		b.first_sticking_date = self._first_needed
		b.run_method("validate")
		return b

	# -------------------------------------------------------------- actions
	@frappe.whitelist()
	def submit_for_approval(self):
		if not self.weeks:
			frappe.throw(_("There is nothing to propagate on this plan."))
		self.status = "Pending Approval"
		self.rejection_reason = None
		self.save()
		return self.status

	@frappe.whitelist()
	def approve(self):
		if not _has_role():
			frappe.throw(_("Only a Farm Manager can approve a propagation plan."))
		if self.status != "Pending Approval":
			frappe.throw(_("Submit the plan for approval first."))
		self.status = "Approved"
		self.approved_by = frappe.session.user
		self.approved_on = now_datetime()
		self.save()
		return self.status

	@frappe.whitelist()
	def reject(self, reason=None):
		if not _has_role():
			frappe.throw(_("Only a Farm Manager can reject a propagation plan."))
		if not (reason or "").strip():
			frappe.throw(_("A rejection reason is required."))
		self.status = "Rejected"
		self.rejection_reason = reason
		self.save()
		return self.status

	@frappe.whitelist()
	def create_motherstock_batch(self):
		"""Raise the real Motherstock Batch for the uncovered requirement."""
		if self.status != "Approved":
			frappe.throw(_("Approve the propagation plan first."))
		peak = self._peak_from_rows()
		first = self._first_from_rows()
		if not peak:
			frappe.throw(_(
				"Existing motherstock already covers every week, so there is nothing "
				"to buy."))
		existing = frappe.db.exists("Summer Flower Motherstock Batch", {
			"production_plan": self.production_plan, "variety": self.variety,
			"farm": self.farm, "docstatus": ["<", 2],
			"first_sticking_date": first})
		if existing:
			return existing
		b = frappe.new_doc("Summer Flower Motherstock Batch")
		b.variety = self.variety
		b.farm = self.farm
		b.protocol = self.protocol
		b.company = self.company
		b.production_plan = self.production_plan
		b.peak_weekly_cuttings = peak
		b.first_sticking_date = first
		b.flags.ignore_permissions = True
		b.insert()
		frappe.db.commit()
		self.db_set("motherstock_batches_created",
		            cint(self.motherstock_batches_created) + 1)
		for r in self.sources:
			if r.source_type == "New Motherstock (TC)" and not r.motherstock_batch:
				frappe.db.set_value("Summer Flower Propagation Source", r.name,
				                    "motherstock_batch", b.name,
				                    update_modified=False)
				break
		return b.name

	def _peak_from_rows(self):
		return max([cint(r.from_new_ms) for r in self.weeks] or [0])

	def _first_from_rows(self):
		dates = [getdate(r.week_start_date) for r in self.weeks
		         if cint(r.from_new_ms) > 0]
		return min(dates) if dates else None

	@frappe.whitelist()
	def create_seedling_requests(self):
		"""One Seedling Request per sticking week for the propagation unit."""
		if self.status != "Approved":
			frappe.throw(_("Approve the propagation plan first."))
		if not frappe.db.exists("DocType", "Seedling Request"):
			frappe.throw(_("Seedling Request is not installed on this site."))
		meta = frappe.get_meta("Seedling Request")
		made = []
		for r in self.weeks:
			if not cint(r.plants_to_stick):
				continue
			week = "%s-W%02d" % (r.year, cint(r.week_no))
			if meta.has_field("custom_propagation_plan") and frappe.db.exists(
					"Seedling Request", {"custom_propagation_plan": self.name,
					                     "custom_sticking_week": week}):
				continue
			sr = frappe.new_doc("Seedling Request")
			if meta.has_field("title"):
				sr.title = _("{0} {1} - {2} plants").format(
					self.variety, week, cint(r.plants_to_stick))
			if meta.has_field("requested_by"):
				sr.requested_by = frappe.session.user
			if meta.has_field("custom_propagation_plan"):
				sr.custom_propagation_plan = self.name
				sr.custom_sticking_week = week
				sr.custom_plants_required = cint(r.plants_to_stick)
			sr.flags.ignore_permissions = True
			sr.flags.ignore_mandatory = True
			sr.insert()
			made.append(sr.name)
		frappe.db.commit()
		self.db_set("seedling_requests_created",
		            cint(self.seedling_requests_created) + len(made))
		return {"created": len(made), "names": made[:10]}


@frappe.whitelist()
def build_from_plan(production_plan):
	"""Create the propagation plan for a production plan."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	existing = frappe.db.exists("Summer Flower Propagation Plan",
	                            {"production_plan": production_plan,
	                             "status": ["!=", "Rejected"]})
	if existing:
		return existing
	d = frappe.new_doc("Summer Flower Propagation Plan")
	d.production_plan = production_plan
	d.flags.ignore_permissions = True
	d.insert()
	frappe.db.commit()
	return d.name
