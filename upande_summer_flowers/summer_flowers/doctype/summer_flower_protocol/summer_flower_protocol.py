# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""The summer flower growing protocol, owned by this app.

Everything here used to live on upande_agriculture's Crop Protocol as eighty-five
custom_sf_ fields. That doctype is the rose master and is being developed as one:
its latest shape drops `variety` and `farm` outright, renames weeks_to_pinch to
weeks_to_first_bending, weeks_between_flushes to flush_interval_weeks and
total_weeks_in_ground to productive_life_weeks, and changes the naming rule. None
of that is wrong for roses. All of it is fatal to a doctype that has to be per
variety AND farm, because the same crop is grown differently at each.

So the two are separate. The fieldnames are unchanged, custom_sf_ prefix and all,
because that prefix is now the only cost of the split -- renaming a hundred fields
would have meant touching every reader of them on the same day as the move, and a
rename is mechanical whenever anyone wants it.

The logic is not duplicated: validate and on_update call the same functions that
served Crop Protocol, so there is one derivation, one approval and one snapshot
writer.
"""

import frappe
from frappe.model.document import Document

from upande_summer_flowers.summer_flowers import crop_protocol


class SummerFlowerProtocol(Document):
	def validate(self):
		crop_protocol.validate(self)

	def on_update(self):
		# A bulk move is not an edit. Copying a protocol that was already approved
		# would otherwise try to snapshot it again and be refused for having changed
		# nothing, which is the right answer to the wrong question.
		if frappe.flags.get("sf_protocol_move"):
			return
		crop_protocol.on_update(self)
