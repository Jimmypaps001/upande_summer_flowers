# Copyright (c) 2026, James Kiruga and contributors
"""Move the summer flower protocols off Crop Protocol onto their own doctype."""

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

@frappe.whitelist()
def move_back(dry_run=1):
	"""Copy summer flower protocols back onto Crop Protocol."""
	dry_run = cint(dry_run)
	if not frappe.db.table_exists("Summer Flower Protocol"):
		return {"source_count": 0, "moved": [], "already": [], "failed": [],
		        "note": "no Summer Flower Protocol on this site"}
	target_meta = frappe.get_meta("Crop Protocol")
	names = frappe.get_all("Summer Flower Protocol", pluck="name")
	moved, already, failed = [], [], []
	# A move is not an edit: without this, copying an already-approved protocol
	frappe.flags.sf_protocol_move = True

	for n in names:
		if frappe.db.exists("Crop Protocol", n):
			already.append(n)
			continue
		src = frappe.get_doc("Summer Flower Protocol", n)
		if dry_run:
			moved.append(n)
			continue
		try:
			new = frappe.new_doc("Crop Protocol")
			for f in target_meta.fields:
				if f.fieldtype in ("Section Break", "Column Break", "Tab Break",
				                   "HTML", "Button"):
					continue
				if f.fieldtype == "Table":
					if not f.fieldname.startswith("custom_sf_"):
						continue
					new.set(f.fieldname, [])
					for row in _rows(src, f.fieldname):
						new.append(f.fieldname, row)
					continue
				val = src.get(f.fieldname)
				if val not in (None, ""):
					new.set(f.fieldname, val)
			new.custom_is_summer_flower = 1
			new.crop_type = "Summer Flowers"
			new.name = n
			new.insert(set_name=n, ignore_permissions=True)
			moved.append(n)
		except Exception as e:
			failed.append((n, str(e)))
	frappe.flags.sf_protocol_move = False

	r = {"source_count": len(names), "moved": moved, "already": already,
	     "failed": failed, "dry_run": dry_run}
	print("summer flower protocols found      : %d" % r["source_count"])
	print("copied onto Crop Protocol          : %d" % len(moved))
	print("already there, left alone          : %d" % len(already))
	print("failed                             : %d" % len(failed))
	for n, e in failed:
		print("    %s : %s" % (n, e[:120]))
	if dry_run:
		print("DRY RUN. Nothing written. Call with dry_run=0 to apply.")
	return r
