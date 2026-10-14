# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Site-wide settings for summer flower planning.

The only thing with rules of its own is the lift profile. A consignment is not
handed over in one go: roots go to the trough, are lifted in waves, and each
wave is a share of the consignment. Those shares are the whole arithmetic --
5,000 roots at fifteen plants each is 75,000 plants, and 45/30/25 is 33,750
then 22,500 then 18,750 -- so a set that does not add to a hundred is not a
rounding question, it is plants invented or lost.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

# A share may miss a hundred by this much and still be rounding rather than a
# mistake: three equal lifts are 33.3 each.
SLACK = 0.5


class SummerFlowerSettings(Document):
	def validate(self):
		self.check_lifts()

	def check_lifts(self):
		"""Each profile adds to a hundred, is numbered once, and runs forwards."""
		groups = {}
		for row in (self.handover_waves or []):
			if not row.form:
				frappe.throw(_("Row {0} of the lifts says nothing about which "
				               "material it is for. Set Tissue Culture or Roots."
				               ).format(row.idx))
			groups.setdefault((row.source or None, row.form), []).append(row)

		for (source, form), rows in sorted(groups.items(), key=lambda x: str(x[0])):
			who = _("{0} {1}").format(source or _("every supplier"), form)
			total = sum(flt(r.share_pct) for r in rows)
			if total > 100 + SLACK:
				frappe.throw(_(
					"The lifts for {0} come to {1}%. A consignment cannot hand "
					"over more than all of itself, so take {2}% off somewhere "
					"before saving."
				).format(who, round(total, 2), round(total - 100, 2)),
					title=_("More than a whole consignment"))
			if total < 100 - SLACK:
				frappe.throw(_(
					"The lifts for {0} come to {1}%, so {2}% of every "
					"consignment is never handed over. Add a lift for it, or "
					"raise the shares until they make a hundred."
				).format(who, round(total, 2), round(100 - total, 2)),
					title=_("Less than a whole consignment"))

			seen = {}
			for r in sorted(rows, key=lambda x: cint(x.lift_no)):
				n = cint(r.lift_no)
				if not n:
					frappe.throw(_("Number the lifts for {0}: first, second, "
					               "and so on.").format(who))
				if n in seen:
					frappe.throw(_(
						"{0} has two lift {1}s. Each lift is one wave off the "
						"consignment, so number them 1, 2, 3."
					).format(who, n))
				seen[n] = r
				if flt(r.share_pct) <= 0:
					frappe.throw(_("Lift {0} of {1} takes no share of the "
					               "consignment. Give it one or remove it."
					               ).format(n, who))

			order = [seen[k] for k in sorted(seen)]
			for a, b in zip(order, order[1:]):
				if cint(b.weeks_after_arrival) <= cint(a.weeks_after_arrival):
					frappe.throw(_(
						"Lift {0} of {1} is {2} weeks after the consignment "
						"arrives and lift {3} is {4}. A later lift comes off "
						"later, so the weeks have to climb."
					).format(cint(b.lift_no), who, cint(b.weeks_after_arrival),
					         cint(a.lift_no), cint(a.weeks_after_arrival)))
