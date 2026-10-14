# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Put the measured propagation losses on Summer Flower Settings.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_propagation_losses.main
	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_propagation_losses.main \\
		--kwargs "{'apply': 1}"

Allowing nothing for losses is a decision, not an oversight -- the provision
has sat at zero on every site because nobody had a figure to put in it. The
Eryngium seedling delivery book has one, and it is two figures rather than
one, because the two forms do not lose alike:

	tissue culture   11.3% lost   of 4,441,971 plantlets over 382 consignments
	roots            13.0% lost   of 2,797,250 roots     over  54 consignments

Only consignments where the book's own arithmetic closes are counted -- what
came out plus what was lost is what went in. 16 rows do not close and 421 have
no intake recorded, and averaging those in is how tissue culture came out at
114% survival the first time this was measured.

The figure agrees with the book's own planning assumption: 0.88 planned
success for 2026, against 0.887 measured.

Rows already set are left alone unless `force` is given, because somebody may
have corrected them from a later season.
"""

import traceback

import frappe
from frappe.utils import cint, flt

# (fieldname, label, measured loss %, what it was measured over)
LOSSES = [
	("tc_loss_pct", "Tissue culture", 11.3,
	 "4,441,971 plantlets over 382 consignments that reconcile"),
	("roots_loss_pct", "Roots", 13.0,
	 "2,797,250 roots over 54 consignments that reconcile"),
]


# The loss belongs to the propagation stage of each chain: it is what happens
# between the material landing at the unit and plants reaching the farm.
ROUTE_LOSS = {"TC": 11.3, "Roots": 13.0}
PROPAGATION = "Propagation"


def main(apply=0, force=0, variety="Eryngium", protocols=1):
	try:
		_run(cint(apply), cint(force))
		if cint(protocols):
			_routes(cint(apply), cint(force), variety)
	except Exception:
		print(traceback.format_exc())


def _routes(apply, force, variety):
	"""Put the same figures on the protocols, where they can be read.

	The engine takes its provision from the settings above; the route carries
	it so the protocol card can SAY what is allowed for, and so the roots
	costing -- which walks the route multiplying out each stage's loss -- works
	on the same number rather than a zero.
	"""
	from upande_summer_flowers.summer_flowers import crop_protocol as cp

	names = frappe.get_all("Crop Protocol",
	                       filters={"name": ["like", "%%%s%%" % variety]},
	                       pluck="name")
	if not names:
		print("\nNo %s protocol on this site." % variety)
		return

	print("\n%s protocols — the loss on each chain's propagation stage" % variety)
	writes = []
	for name in sorted(names):
		doc = frappe.get_doc("Crop Protocol", name)
		chains = cp.route_chains(doc) or []
		if not chains:
			print("   %-48s (no route)" % name[:48])
			continue
		bits = []
		for chain in chains:
			entry = chain[0][0]
			want = ROUTE_LOSS.get((entry.get("stage") or "").strip())
			if want is None:
				continue
			# The propagation stage of THIS chain, not the first one on the
			# document: a route with two ways in has two of them.
			prop = next((st for st, _steps in chain
			             if (st.get("stage") or "") == PROPAGATION), None)
			if prop is None:
				bits.append("%s: no propagation stage" % entry.get("stage"))
				continue
			cur = flt(prop.get("loss_pct"))
			if cur and not force:
				bits.append("%s: %s%% already set" % (entry.get("stage"), cur))
				continue
			if abs(cur - want) < 1e-9:
				bits.append("%s: already %s%%" % (entry.get("stage"), want))
				continue
			bits.append("%s: %s%% -> %s%%" % (entry.get("stage"), cur, want))
			writes.append((prop.name, want))
		print("   %-48s %s" % (name[:48], "; ".join(bits) or "nothing to set"))

	if not writes:
		print("\n   Nothing to change on the routes.")
		return
	if not apply:
		print("\n   (dry run — %d route rows would change)" % len(writes))
		return
	for rowname, want in writes:
		frappe.db.set_value("Crop Material Stage", rowname, "loss_pct", want,
		                    update_modified=False)
	frappe.db.commit()
	print("\n   %d route rows updated. Approve each protocol to write a new "
	      "version — the plans already built keep the version they were built "
	      "on." % len(writes))


def _run(apply, force):
	s = frappe.get_single("Summer Flower Settings")
	print("Summer Flower Settings as it stands")
	print("   %-22s %s" % ("fallback provision",
	                       flt(s.get("propagation_loss_pct"))))
	for field, label, _want, _basis in LOSSES:
		print("   %-22s %s" % (label, flt(s.get(field))))

	todo = []
	for field, label, want, basis in LOSSES:
		cur = flt(s.get(field))
		if cur and not force:
			print("\n%s already set to %s%% — leaving it (pass force=1 to replace)"
			      % (label, cur))
			continue
		todo.append((field, label, want, basis))

	if not todo:
		print("\nNothing to do.")
		return

	print("\n%s" % ("writing" if apply else "would write"))
	for field, label, want, basis in todo:
		print("   %-16s %5s%% lost   measured over %s" % (label, want, basis))
	if not apply:
		print("\n(dry run — nothing written)")
		return

	for field, _label, want, _basis in todo:
		frappe.db.set_single_value("Summer Flower Settings", field, want)
	frappe.db.commit()

	print("\nWritten. What it means for an order:")
	from upande_summer_flowers.summer_flowers import tc_supply as ts

	for form in ("Tissue Culture", "Roots"):
		keep = ts.survival(form)
		print("   %-16s %5.1f%% reach the field, so 1,000,000 wanted is "
		      "%s ordered" % (form, keep * 100,
		                      "{:,}".format(int(round(1_000_000 / keep)))))
