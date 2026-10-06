# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Put the standard lead times on every route row that has none.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_lead_times.main
	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_lead_times.main \\
		--kwargs "{'apply': 1}"

Roots are a year and tissue culture is fifteen weeks. Those are properties of
the material rather than of a farm, so they are the same on every protocol and
there is no reason for anybody to type them in one at a time.

Only rows sitting at zero are touched. A protocol where somebody has already put
a real figure in is left exactly as it is and reported, so this is safe to run
twice and safe to run after a correction.
"""

import frappe
from frappe.utils import cint

from upande_summer_flowers.summer_flowers.crop_routes import LEAD_WEEKS


def main(apply=0, variety=None, force=0):
	"""`force` replaces figures somebody already typed, printing the old ones."""
	apply, force = cint(apply), cint(force)
	rows = frappe.get_all(
		"Crop Material Stage",
		filters={"stage": ["in", list(LEAD_WEEKS)]},
		fields=["name", "parent", "parenttype", "stage", "lead_weeks",
		        "is_purchase"])

	todo, kept, skipped = [], [], []
	for r in rows:
		if variety and variety.lower() not in (r.parent or "").lower():
			continue
		want = LEAD_WEEKS[r.stage]
		if cint(r.lead_weeks) == want:
			kept.append(r)
		elif cint(r.lead_weeks):
			(todo if force else skipped).append((r, want) if force else r)
		else:
			todo.append((r, want))

	print("stages that carry a standard lead: %s"
	      % ", ".join("%s=%d weeks" % (k, v) for k, v in LEAD_WEEKS.items()))
	print("\n%d row(s) already right" % len(kept))
	print("%d row(s) to set" % len(todo))
	print("%d row(s) left alone because somebody typed a figure" % len(skipped))
	for r in skipped[:10]:
		print("   kept %-46s %-6s %s weeks" % (r.parent, r.stage, r.lead_weeks))
	if force:
		changed = [(r, w) for r, w in todo if cint(r.lead_weeks)]
		if changed:
			print("\n%d row(s) will have a typed figure REPLACED:" % len(changed))
			for r, w in changed[:12]:
				print("   %-46s %-6s %s -> %s weeks"
				      % (r.parent, r.stage, r.lead_weeks, w))

    # Grouped so the output is readable: one line per protocol, not per row.
	by_parent = {}
	for r, want in todo:
		by_parent.setdefault(r.parent, []).append((r.stage, want))
	for parent in sorted(by_parent)[:20]:
		print("   set  %-46s %s" % (parent, ", ".join(
			"%s -> %d" % (s, w) for s, w in by_parent[parent])))
	if len(by_parent) > 20:
		print("   ... and %d more" % (len(by_parent) - 20))

	if not apply:
		print("\n(dry run — pass --kwargs \"{'apply': 1}\" to write)")
		return

	for r, want in todo:
		frappe.db.set_value("Crop Material Stage", r.name, "lead_weeks", want,
		                    update_modified=False)
		frappe.clear_document_cache(r.parenttype, r.parent)
	frappe.db.commit()
	print("\nwritten: %d row(s) across %d protocol(s)"
	      % (len(todo), len(by_parent)))
	# Every parent that HAS a bought row, not only the ones this run touched: a
	# second run would otherwise push nothing, because the rows were already
	# right and the parents were still wrong.
	_push_to_parents(sorted({r.parent for r in rows}))


def _push_to_parents(parents):
	"""Carry the bought row's lead up to the parent's own supplier-lead field.

	A Crop Protocol Version derives supplier_lead_weeks from its bought row when
	it is MINTED, and that is the right place for it -- but every version minted
	before the rows carried a lead still holds a zero, and the motherstock engine
	reads the field rather than the row. So the same derivation is applied to the
	records that predate the data, or the two disagree: Aster's route said
	fifteen weeks while its order date was still worked out from nothing.
	"""
	moved = 0
	for parent in parents:
		dt = ("Crop Protocol Version"
		      if frappe.db.exists("Crop Protocol Version", parent)
		      else "Crop Protocol")
		meta = frappe.get_meta(dt)
		field = next((f.fieldname for f in meta.fields
		              if "supplier_lead" in f.fieldname), None)
		if not field:
			continue
		row = frappe.db.get_value(
			"Crop Material Stage",
			{"parent": parent, "is_purchase": 1, "lead_weeks": [">", 0]},
			"lead_weeks", order_by="idx")
		if not row or cint(frappe.db.get_value(dt, parent, field)) == cint(row):
			continue
		frappe.db.set_value(dt, parent, field, cint(row), update_modified=False)
		frappe.clear_document_cache(dt, parent)
		moved += 1
	frappe.db.commit()
	print("supplier lead carried up to %d parent record(s)" % moved)
