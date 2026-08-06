# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Blocks and beds that belong to a farm rather than to a greenhouse.

Summer flowers are not grown under glass. They are grown in blocks, in the open,
and the blocks hold beds; greenhouses at this farm are for roses -- sprays and
standards. Block and Bed both come from apps that assume the opposite: Block names
itself "{greenhouse} - Block {block}", Bed requires a greenhouse and validates that
its farm is marked "Has Beds" through that greenhouse.

Nothing here changes how a greenhouse block behaves. Where there is a greenhouse the
original naming and the original validation apply exactly as before; only a block
with no greenhouse takes the farm-based path.
"""

import frappe
from frappe import _


def block_autoname(doc, method=None):
	"""Name a greenhouse-less block after its farm.

	frappe.model.naming.set_new_name runs this hook before it falls back to the
	doctype's format string, and only uses the format string if the hook left the
	name unset -- so a greenhouse block still names itself exactly as it always did.
	"""
	if doc.name or doc.get("greenhouse"):
		return
	if not doc.get("farm") or not doc.get("block"):
		frappe.throw(_("A block with no greenhouse needs a farm and a block name, "
		               "because that is what it is named after."))
	doc.name = "{0} - Block {1}".format(doc.farm, doc.block)


def bed_autoname(doc, method=None):
	"""Name a greenhouse-less bed after the block that holds it."""
	if doc.name or doc.get("greenhouse"):
		return
	if not doc.get("custom_block"):
		frappe.throw(_("A bed with no greenhouse must belong to a block, because that "
		               "is what it is named after."))
	doc.name = "{0} - Bed {1}".format(doc.custom_block, doc.get("bed") or 0)


def bed_farm_from_block(doc, method=None):
	"""A greenhouse-less bed takes its farm from its block.

	Bed.farm is empty on all 20,668 beds on this site because it was only ever
	reachable through the greenhouse. A block knows its farm, so a bed in a block
	knows it too, and space becomes attributable without going through a warehouse.
	"""
	if doc.get("greenhouse") or not doc.get("custom_block"):
		return
	if not doc.get("farm"):
		doc.farm = frappe.db.get_value("Block", doc.custom_block, "farm")
