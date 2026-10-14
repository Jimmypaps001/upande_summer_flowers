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


# The propagation stage's loss is NOT a field somebody types: the protocol
# derives it as 1 - rooting_success x field_establishment every time it is
# saved. Writing 11.3 onto the child row looked right and did not survive the
# next save, which is the sort of fix that is worse than none.
#
# So the CAUSE is set instead. The two chains share one rooting figure, so the
# protocol carries the weighted measurement across both forms:
#
#   (4,441,971 x 11.3% + 2,797,250 x 13.0%) / 7,239,221 = 12.0%
#
# and 88.0% rooting against 100% establishment is exactly that. The per-form
# precision -- 11.3 on tissue culture, 13.0 on roots -- lives on the settings
# above, which is what the ordering engine reads.
ROOTING_SUCCESS = 88.0
WEIGHTED_LOSS = 12.0
PROPAGATION = "Propagation"


def main(apply=0, force=0, variety="Eryngium", protocols=1):
	try:
		_run(cint(apply), cint(force))
		if cint(protocols):
			_routes(cint(apply), cint(force), variety)
	except Exception:
		print(traceback.format_exc())


def _routes(apply, force, variety):
	"""Put the cause of the loss on the protocols, where it is derived from.

	The engine takes its provision from the settings above; the protocol
	carries rooting success so the route can SAY what is allowed for and the
	roots costing -- which walks the route multiplying out each stage's loss --
	works on a real number rather than a zero.
	"""
	names = frappe.get_all("Crop Protocol",
	                       filters={"name": ["like", "%%%s%%" % variety]},
	                       pluck="name")
	if not names:
		print("\nNo %s protocol on this site." % variety)
		return

	print("\n%s protocols — rooting success, which is what the propagation "
	      "loss is derived from" % variety)
	writes = []
	for name in sorted(names):
		cur = flt(frappe.db.get_value("Crop Protocol", name,
		                              "custom_sf_rooting_success_pct"))
		est = flt(frappe.db.get_value("Crop Protocol", name,
		                              "custom_sf_field_establishment_pct")) or 100.0
		derived = round((1 - cur / 100.0 * est / 100.0) * 100, 2) if cur else None
		if cur and abs(cur - ROOTING_SUCCESS) < 1e-9:
			print("   %-48s already %s%% (loss %s%%)" % (name[:48], cur, derived))
			continue
		if cur and cur != 100.0 and not force:
			print("   %-48s %s%% set by somebody — leaving it (force=1 to replace)"
			      % (name[:48], cur))
			continue
		print("   %-48s %s%% -> %s%%   (propagation loss %s%% -> %s%%)" % (
			name[:48], cur, ROOTING_SUCCESS, derived if derived is not None else 0,
			WEIGHTED_LOSS))
		writes.append(name)

	if not writes:
		print("\n   Nothing to change on the routes.")
		return
	if not apply:
		print("\n   (dry run — %d route rows would change)" % len(writes))
		return
	for name in writes:
		doc = frappe.get_doc("Crop Protocol", name)
		doc.custom_sf_rooting_success_pct = ROOTING_SUCCESS
		doc.flags.ignore_permissions = True
		# Saved through the controller on purpose: it is the save that derives
		# the propagation loss from this, on every chain the route has.
		doc.save()
	frappe.db.commit()
	print("\n   %d protocols updated, and the loss re-derived on every chain. "
	      "Approve each one to write a new version — the plans already built "
	      "keep the version they were built on." % len(writes))
	for name in writes[:3]:
		doc = frappe.get_doc("Crop Protocol", name)
		print("      %-44s %s" % (name[:44], [
			(r.stage, r.loss_pct) for r in doc.get("custom_sf_material_route")
			if r.stage == PROPAGATION]))


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
