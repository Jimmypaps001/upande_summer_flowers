# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""A way plant material becomes a plant in the ground: TC -> Motherstock -> Plants.

Held as data so a new way in is a new record, not a code change. A protocol picks
one or more of these as its ways in (Crop Protocol Way).
"""

import frappe
from frappe import _
from frappe.model.document import Document


class MaterialRoute(Document):
	def validate(self):
		from upande_summer_flowers.summer_flowers.crop_protocol import (
			BOUGHT_STAGES,
			ROUTE_END,
			ROUTE_ENTRY,
		)

		stages = [r.stage for r in self.stages if r.stage]
		if not stages:
			frappe.throw(_("A route needs its stages."))
		if stages[0] not in ROUTE_ENTRY:
			frappe.throw(_("A route starts with the material that is bought or taken: {0}. "
			               "{1} cannot be the first stage.").format(
				", ".join(ROUTE_ENTRY), frappe.bold(stages[0])))
		if stages[-1] != ROUTE_END:
			frappe.throw(_("Every route ends at {0}, because that is what goes in the "
			               "ground.").format(frappe.bold(ROUTE_END)))
		if any(s in ROUTE_ENTRY or s == ROUTE_END for s in stages[1:-1]):
			frappe.throw(_("Only the first stage can be bought or taken, and only the last "
			               "can be {0}.").format(ROUTE_END))
		self.summary = " → ".join(stages)
		# Only a route that starts by buying has a supplier, so only it has a lead
		# time or a price. Own cuttings, roots and tubers are taken, not ordered.
		self.bought = 1 if stages[0] in BOUGHT_STAGES else 0
