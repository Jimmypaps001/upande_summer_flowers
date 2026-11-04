# Copyright (c) 2026, James Kiruga and contributors
"""One thing that actually happened at the propagation unit."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate

# What each event moves. A "Loss" takes its form from what was lost, so it is
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
		"""Rebuild the plan so the variance moves with this record."""
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
