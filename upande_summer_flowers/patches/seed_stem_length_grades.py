# Copyright (c) 2026, James Kiruga and contributors
"""Turn the free-text stem length grades into records, and point both tables at them."""

import re

import frappe

TABLES = (
	("Summer Flower Demand Grade", "grade"),
	("Summer Flower Protocol Grade", "grade"),
)


def _canonical(value):
	"""'50' and '50cm' are the same grade. The one with the unit is the name."""
	v = (value or "").strip()
	if not v:
		return None
	if re.fullmatch(r"\d+", v):
		return "%scm" % v
	return v


def execute():
	if not frappe.db.table_exists("Summer Flower Grade"):
		return

	seen = {}
	for doctype, field in TABLES:
		if not frappe.db.table_exists(doctype):
			continue
		for row in frappe.db.sql(
			"select distinct `%s` as v from `tab%s` where ifnull(`%s`, '') != ''"
			% (field, doctype, field), as_dict=True
		):
			name = _canonical(row.v)
			if name:
				seen.setdefault(name, set()).add(row.v)

	created = 0
	for name in sorted(seen):
		if frappe.db.exists("Summer Flower Grade", name):
			continue
		found = re.search(r"\d+", name)
		doc = frappe.new_doc("Summer Flower Grade")
		doc.grade_name = name
		doc.length_cm = int(found.group()) if found else 0
		doc.flags.ignore_permissions = True
		doc.insert()
		created += 1

	# Rewrite the rows whose spelling is being folded away. db_set on the table
	fixed = 0
	for doctype, field in TABLES:
		if not frappe.db.table_exists(doctype):
			continue
		for name, spellings in seen.items():
			for old in spellings:
				if old == name:
					continue
				n = frappe.db.sql(
					"update `tab%s` set `%s` = %%s where `%s` = %%s"
					% (doctype, field, field), (name, old))
				fixed += 1
				print("   %s: %r -> %r" % (doctype, old, name))

	frappe.db.commit()
	print("seeded %d stem length grades, folded %d spellings" % (created, fixed))
