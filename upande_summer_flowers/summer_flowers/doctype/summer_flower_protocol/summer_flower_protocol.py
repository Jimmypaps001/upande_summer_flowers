# Copyright (c) 2026, James Kiruga and contributors
"""The summer flower growing protocol, owned by this app."""

import frappe
from frappe.model.document import Document

from upande_summer_flowers.summer_flowers import crop_protocol


class SummerFlowerProtocol(Document):
	def validate(self):
		crop_protocol.validate(self)

	def on_update(self):
		# A bulk move is not an edit. Copying a protocol that was already approved
		if frappe.flags.get("sf_protocol_move"):
			return
		crop_protocol.on_update(self)
