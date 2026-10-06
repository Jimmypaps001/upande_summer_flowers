# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Put the lift return on the Roots row, and carry it onto a version.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_root_return.main
	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_root_return.main \\
		--kwargs "{'apply': 1}"

One root becomes fifteen plants. That is not a guess: Eryngium Production
Plannning.xlsx, sheet "Roots Planning", sets 84,500 roots against 1,267,500
expected plants, and the same fifteen holds in each of its three columns
(738,900/49,260 and 528,600/35,240).

Without it a plan can only be ordered as tissue culture. The card says so --
"the protocol does not say what a root becomes" -- and refuses to put a number
on the roots half rather than printing a confident zero.

Setting it on the protocol is not enough on its own. A plan reads the VERSION,
which is a snapshot, so this mints one; and a plan already approved keeps the
version it was built on, which is the point of a snapshot and not a fault.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

#: Roots Planning, Eryngium Production Plannning.xlsx.
RETURNS_PER_PLANT = 15.0
LIFT_STAGE = "Roots"


def main(apply=0, variety="Eryngium", mint=1):
	apply, mint = cint(apply), cint(mint)
	protocols = frappe.get_all(
		"Crop Protocol",
		filters={"custom_is_summer_flower": 1, "name": ["like", "%%%s%%" % variety]},
		fields=["name", "farm", "custom_sf_current_version"])

	todo, have = [], []
	for p in protocols:
		rows = frappe.get_all(
			"Crop Material Stage",
			filters={"parent": p.name, "stage": LIFT_STAGE},
			fields=["name", "returns_per_plant"])
		for r in rows:
			(have if flt(r.returns_per_plant) > 0 else todo).append((p, r))

	print("one root becomes %g plants (Roots Planning: 1,267,500 / 84,500)"
	      % RETURNS_PER_PLANT)
	print("\n%d Roots row(s) already carry a figure" % len(have))
	for p, r in have[:6]:
		print("   kept %-46s %s" % (p.name[:46], r.returns_per_plant))
	print("%d Roots row(s) to set" % len(todo))
	for p, r in todo[:12]:
		print("   set  %-46s -> %g" % (p.name[:46], RETURNS_PER_PLANT))
	if len(todo) > 12:
		print("   ... and %d more" % (len(todo) - 12))

    # Protocols with no Roots row at all cannot take the figure; name them.
	withroots = {p.name for p, _r in todo} | {p.name for p, _r in have}
	missing = [p.name for p in protocols if p.name not in withroots]
	if missing:
		print("\n%d protocol(s) have no Roots row, so there is nothing to set:"
		      % len(missing))
		for n in missing[:6]:
			print("   %s" % n)

	if not apply:
		print("\n(dry run — pass --kwargs \"{'apply': 1}\" to write)")
		return

	for p, r in todo:
		frappe.db.set_value("Crop Material Stage", r.name,
		                    "returns_per_plant", RETURNS_PER_PLANT,
		                    update_modified=False)
		frappe.clear_document_cache("Crop Protocol", p.name)
	frappe.db.commit()
	print("\nset on %d row(s)" % len(todo))

	if not mint:
		print("not minting; a plan will not see this until the protocol is approved")
		return
	_mint({p.name for p, _r in todo})


def _mint(names):
	"""Approve each protocol so a version carries the figure."""
	from upande_summer_flowers.summer_flowers import crop_protocol as cp

	done, refused = [], []
	for n in sorted(names):
		try:
			d = frappe.get_doc("Crop Protocol", n)
			if not d.get("custom_sf_change_reason"):
				d.custom_sf_change_reason = _(
					"Lift return set to %g plants a root, from the farm's own "
					"Roots Planning sheet." % RETURNS_PER_PLANT)
				d.flags.ignore_permissions = True
				d.flags.ignore_mandatory = True
				d.save()
			done.append((n, cp.approve(n)))
		except Exception as e:
			refused.append((n, frappe.utils.strip_html(str(e))[:120]))
	for n, v in done:
		print("   minted %-46s %s" % (n[:46], v))
	for n, why in refused:
		print("   REFUSED %-45s %s" % (n[:45], why))
