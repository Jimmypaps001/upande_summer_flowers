# Copyright (c) 2026, James Kiruga and contributors
"""Blocks and beds that belong to a farm rather than to a greenhouse."""

import frappe
from frappe import _


def block_autoname(doc, method=None):
	"""Name a greenhouse-less block after its farm."""
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
	"""A greenhouse-less bed takes its farm from its block."""
	if doc.get("greenhouse") or not doc.get("custom_block"):
		return
	if not doc.get("farm"):
		doc.farm = frappe.db.get_value("Block", doc.custom_block, "farm")
