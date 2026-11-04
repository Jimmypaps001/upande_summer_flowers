# Copyright (c) 2026, James Kiruga and contributors
"""Bench commands for this app."""

import click
from frappe.commands import get_site, pass_context


@click.command("check-customizations")
@pass_context
def check_customizations(context):
	"""Name the customization file that will break the next migrate or install."""
	import frappe

	from upande_summer_flowers.summer_flowers.customization_check import check

	frappe.init(site=get_site(context))
	frappe.connect()
	try:
		problems = check()
	finally:
		frappe.destroy()

	if any(p["breaks"] == "migrate" for p in problems):
		raise SystemExit(1)


commands = [check_customizations]
