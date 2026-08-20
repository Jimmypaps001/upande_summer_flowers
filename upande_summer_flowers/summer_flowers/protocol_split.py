# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Move the summer flower protocols off Crop Protocol onto their own doctype.

A straight copy, field for field, because the split deliberately kept every
fieldname: what was custom_sf_x on Crop Protocol is custom_sf_x here. The name is
kept too -- "<variety>-<farm>" -- so every Crop Protocol Version, production plan
and crop cycle that points at a protocol still points at one by the same name.

Nothing is deleted. The Crop Protocol records stay where they are until someone
who can see both is satisfied, which is the only safe order for a move that
carries the numbers every plan is built from.
"""

import frappe
from frappe.utils import cint

SKIP = ("name", "owner", "creation", "modified", "modified_by", "docstatus", "idx",
        "doctype", "parent", "parentfield", "parenttype")


def _rows(doc, fieldname):
	out = []
	for r in (doc.get(fieldname) or []):
		out.append({k: v for k, v in r.as_dict().items() if k not in SKIP})
	return out


@frappe.whitelist()
def move(dry_run=1):
	"""Copy every summer flower Crop Protocol to Summer Flower Protocol."""
	dry_run = cint(dry_run)
	target_meta = frappe.get_meta("Summer Flower Protocol")
	names = frappe.get_all("Crop Protocol", filters={"custom_is_summer_flower": 1},
	                       pluck="name")
	moved, already, failed = [], [], []
	# The move copies; it does not approve. See SummerFlowerProtocol.on_update.
	frappe.flags.sf_protocol_move = True

	for n in names:
		if frappe.db.exists("Summer Flower Protocol", n):
			already.append(n)
			continue
		src = frappe.get_doc("Crop Protocol", n)
		if dry_run:
			moved.append(n)
			continue
		try:
			new = frappe.new_doc("Summer Flower Protocol")
			for f in target_meta.fields:
				if f.fieldtype in ("Section Break", "Column Break", "Tab Break",
				                   "HTML", "Button"):
					continue
				if f.fieldtype == "Table":
					new.set(f.fieldname, [])
					for row in _rows(src, f.fieldname):
						new.append(f.fieldname, row)
					continue
				new.set(f.fieldname, src.get(f.fieldname))
			# Keep the name: everything downstream points at it.
			new.flags.name_set = True
			new.name = n
			new.flags.ignore_permissions = True
			new.flags.ignore_mandatory = True
			new.flags.ignore_validate = True
			new.insert()
			moved.append(n)
		except Exception as e:
			failed.append("%s: %s" % (n, str(e)[:120]))

	frappe.flags.sf_protocol_move = False
	if not dry_run:
		frappe.db.commit()
	return {"dry_run": bool(dry_run), "moved": moved, "already_there": already,
	        "failed": failed, "source_count": len(names)}


def report(dry_run=1):
	r = move(dry_run=dry_run)
	print("%s\n" % ("DRY RUN, nothing written" if r["dry_run"] else "WRITTEN"))
	print("summer flower Crop Protocols found : %d" % r["source_count"])
	print("copied                             : %d" % len(r["moved"]))
	print("already on the new doctype         : %d" % len(r["already_there"]))
	print("failed                             : %d" % len(r["failed"]))
	for x in r["failed"][:10]:
		print("   ", x)
	return r
