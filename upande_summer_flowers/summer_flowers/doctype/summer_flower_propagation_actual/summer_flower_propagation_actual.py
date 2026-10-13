# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""One thing that actually happened at the propagation unit.

A consignment arrived, roots came out of the cold store, plants went to the
farm, or something died. The plan says what should happen; this says what did.

It is submittable for the same reason a Purchase Receipt is: a figure that can
be edited after the fact is a figure nobody can be held to, and a delivery that
turns out to be wrong is cancelled and amended rather than quietly retyped. The
propagation plan reads back from submitted records only, so a cancel corrects
the variance instead of leaving it to drift.

It is NOT typed for material the stock ledger already carries. Roots are real
stock on a real item, and propagation units are warehouses, so a transfer out
of the cold store IS the withdrawal -- propagation_actuals.py reads it. What is
typed here is everything the ledger does not have: an outside propagator's
tissue culture, a hand-over nobody raised a stock entry for, a loss.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate

# What each event moves. A "Loss" takes its form from what was lost, so it is
# the one event that cannot be read off the event name alone.
FORM_OF = {
	"TC Received": "Tissue Culture",
	"Roots Withdrawn": "Roots",
	"Plants To Farm": "Plants",
	"Replan Uplift": None,
}


class SummerFlowerPropagationActual(Document):
	def validate(self):
		self.set_week()
		self.set_form()
		self.check_quantities()

	def set_week(self):
		if not self.actual_date:
			return
		y, w, _dow = getdate(self.actual_date).isocalendar()
		self.week_iso = "%d-W%02d" % (y, w)

	def set_form(self):
		form = FORM_OF.get(self.event)
		if form:
			self.form = form
		elif not self.form:
			# A loss or an uplift has to say what it was. Guessing it from the
			# quantities -- units means material, plants means plants -- is the
			# kind of inference that is right until the day somebody records a
			# loss of rooted plants in units.
			self.form = "Plants" if cint(self.plants) and not cint(self.units) else None

	def check_quantities(self):
		if cint(self.units) < 0 or cint(self.plants) < 0:
			frappe.throw(_("Quantities are counts of things that arrived or left. "
			               "A negative one is a cancellation, which is what "
			               "cancelling this record does."))
		if not (cint(self.units) or cint(self.plants)):
			frappe.throw(_("Record how many. A {0} of nothing is not an event."
			               ).format(self.event or _("movement")))
		if self.event == "Loss" and not (self.reason or "").strip():
			frappe.throw(_("Say what the plants were lost to. A loss with no "
			               "reason cannot be planned against next season."))

	def on_submit(self):
		self.touch_plan()

	def on_cancel(self):
		self.touch_plan()

	def touch_plan(self):
		"""Rebuild the plan so the variance moves with this record.

		Never fatal. An actual that is true is still true if the plan it feeds
		cannot be rebuilt right now, and refusing the submit would mean the unit
		cannot record what happened because a different document is unhappy.
		"""
		if not self.propagation_plan:
			return
		try:
			plan = frappe.get_doc("Summer Flower Propagation Plan",
			                      self.propagation_plan)
			if plan.docstatus:
				return
			plan.flags.ignore_permissions = True
			plan.save()
		except Exception as e:
			frappe.clear_last_message()
			frappe.msgprint(
				_("Recorded. The propagation plan could not be brought up to "
				  "date with it just now: {0}").format(
					frappe.utils.strip_html(str(e))[:160]),
				indicator="orange")
