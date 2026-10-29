# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Plantings that did not go in on the day the plan asked for.

Three fields on the production plan used to carry this -- how many slipped, the
worst one, and a sentence about it -- recounted on every save of a document
whose own figures deliberately never move. The gap between plan and ground is
worth reading across plans and over time, which a form cannot show and a list
can, so it is read off the calendars themselves.
"""

import frappe
from frappe import _
from frappe.utils import cint


def execute(filters=None):
	filters = filters or {}
	where = {"actual_planting_date": ["is", "set"]}
	for f in ("farm", "variety", "block"):
		if filters.get(f):
			where[f] = filters[f]
	if filters.get("production_plan"):
		links = frappe.get_all(
			"Summer Flower Plan Block",
			filters={"parent": filters["production_plan"],
			         "existing_planting": ["is", "set"]},
			pluck="existing_planting")
		if not links:
			return columns(), [], _("This plan has no plantings on the ground yet.")
		where["name"] = ["in", links]

	rows = frappe.get_all(
		"Planting Calendar", filters=where, order_by="planting_date asc",
		fields=["name as planting", "farm", "block", "variety", "beds", "plants",
		        "planting_date", "actual_planting_date", "planting_slip_days",
		        "calendar_status"])
	# The worst slip first, because that is the one somebody has to do something
	# about; sorted here rather than in SQL, which will not order on an absolute.
	rows = [r for r in rows if cint(r.planting_slip_days)]
	rows.sort(key=lambda r: abs(cint(r.planting_slip_days)), reverse=True)
	for r in rows:
		days = cint(r.planting_slip_days)
		r["moved"] = (_("{0} days late").format(days) if days > 0
		              else _("{0} days early").format(abs(days)))

	late = sum(1 for r in rows if cint(r.planting_slip_days) > 0)
	early = len(rows) - late
	message = (_("{0} plantings did not go in on the planned day: {1} late, {2} "
	             "early. Their harvest weeks move with them -- the calendars and "
	             "their crop cycles carry the real dates, and the plans they came "
	             "from still say what was approved."
	             ).format(len(rows), late, early) if rows else
	           _("Every planting went in on the day its plan asked for."))
	return columns(), rows, message


def columns():
	return [
		{"fieldname": "planting", "label": _("Planting"), "fieldtype": "Link",
		 "options": "Planting Calendar", "width": 160},
		{"fieldname": "farm", "label": _("Farm"), "fieldtype": "Link",
		 "options": "Farm", "width": 130},
		{"fieldname": "block", "label": _("Block"), "fieldtype": "Link",
		 "options": "Block", "width": 160},
		{"fieldname": "variety", "label": _("Variety"), "fieldtype": "Link",
		 "options": "Item", "width": 180},
		{"fieldname": "planting_date", "label": _("Planned"), "fieldtype": "Date",
		 "width": 105},
		{"fieldname": "actual_planting_date", "label": _("Actual"),
		 "fieldtype": "Date", "width": 105},
		{"fieldname": "planting_slip_days", "label": _("Slip (days)"),
		 "fieldtype": "Int", "width": 100},
		{"fieldname": "moved", "label": _("Moved"), "fieldtype": "Data",
		 "width": 130},
		{"fieldname": "beds", "label": _("Beds"), "fieldtype": "Int", "width": 80},
		{"fieldname": "plants", "label": _("Plants"), "fieldtype": "Int",
		 "width": 100},
		{"fieldname": "calendar_status", "label": _("Status"), "fieldtype": "Data",
		 "width": 120},
	]
