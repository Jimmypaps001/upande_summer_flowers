# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Bench commands for this app.

frappe.utils.bench_helper.get_app_commands imports this module for every installed
app and reads the `commands` list off it, which is why the module must stay import
safe: anything raising at import time prints a traceback in front of every bench
command on the bench, not just this one. Nothing heavier than click is imported here.
"""

import click
from frappe.commands import get_site, pass_context


@click.command("check-customizations")
@pass_context
def check_customizations(context):
	"""Name the customization file that will break the next migrate or install.

	Exits non-zero when something would break migrate, so it can gate a deploy
	rather than only explain one after the fact.
	"""
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
