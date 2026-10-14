# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Put the lift profile on the protocols that have a route to hang it on.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_protocol_lifts.main
	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_protocol_lifts.main \\
		--kwargs "{'apply': 1}"

The table is only worth having filled in. Each lift states its weeks in the
unit and its weeks rooting, and those two add to the week the plants are
ready -- so the farm's roots protocol reads

	lift 1   8 weeks on trough + 7 rooting = to the farm at 15   45%
	lift 2  10                     + 7         =           17   30%
	lift 3  12                     + 7         =           19   25%

and 5,000 roots at fifteen plants each hand over 33,750, then 22,500, then
18,750.

Tissue culture is hardened rather than rooted, so its weeks are all time in
the unit: 7 weeks for the first lift and 8 for the second, which is what the
Eryngium delivery book records.

A protocol that already states lifts is left alone unless `force` is given.
"""

import traceback

import frappe
from frappe.utils import cint

# form -> [(lift, weeks on trough, weeks rooting, share %)]
PROFILE = {
	"Roots": [(1, 8, 7, 45.0), (2, 10, 7, 30.0), (3, 12, 7, 25.0)],
	"Tissue Culture": [(1, 7, 0, 87.5), (2, 8, 0, 12.5)],
}
STAGE_FORM = {"Roots": "Roots", "TC": "Tissue Culture"}


def main(apply=0, variety="Eryngium", force=0):
	try:
		_run(cint(apply), variety, cint(force))
	except Exception:
		print(traceback.format_exc())


def _run(apply, variety, force):
	from upande_summer_flowers.summer_flowers import crop_protocol as cp

	names = frappe.get_all("Crop Protocol",
	                       filters={"name": ["like", "%%%s%%" % variety]},
	                       pluck="name")
	if not names:
		print("No %s protocol on this site." % variety)
		return

	todo = []
	for name in sorted(names):
		doc = frappe.get_doc("Crop Protocol", name)
		have = doc.get("custom_sf_lifts") or []
		# Only the ways in that this protocol actually has.
		forms = []
		for r in (cp.route_rows(doc) or []):
			f = STAGE_FORM.get((r.get("stage") or "").strip())
			if f and f not in forms:
				forms.append(f)
		if not forms:
			print("   %-48s (no TC or Roots on the route)" % name[:48])
			continue
		if have and not force:
			print("   %-48s already states %d lifts — leaving it" % (
				name[:48], len(have)))
			continue
		todo.append((name, forms))
		print("   %-48s %s" % (name[:48], ", ".join(
			"%s: %d lifts" % (f, len(PROFILE[f])) for f in forms)))

	if not todo:
		print("\nNothing to do.")
		return
	if not apply:
		print("\n(dry run — %d protocols would be filled in)" % len(todo))
		return

	for name, forms in todo:
		doc = frappe.get_doc("Crop Protocol", name)
		doc.set("custom_sf_lifts", [])
		for form in forms:
			for n, unit, rooting, share in PROFILE[form]:
				doc.append("custom_sf_lifts", {
					"form": form, "lift_no": n, "trough_weeks": unit,
					"rooting_weeks": rooting, "share_pct": share})
		doc.flags.ignore_permissions = True
		doc.save()
	frappe.db.commit()
	print("\n%d protocols filled in. Approve each one to carry it onto a new "
	      "version — plans already built keep the profile they were built on."
	      % len(todo))
	doc = frappe.get_doc("Crop Protocol", todo[0][0])
	print("\n   %s now reads" % todo[0][0])
	for r in doc.custom_sf_lifts:
		print("      %-16s lift %s  %2dw on trough + %dw rooting = to the farm at %2d"
		      "   %.0f%%" % (r.form, r.lift_no, cint(r.trough_weeks),
		                     cint(r.rooting_weeks), cint(r.weeks_after_arrival),
		                     r.share_pct))
