# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Give every protocol the bed size its own farm actually uses.

	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_bed_geometry.main
	bench --site <site> execute \\
		upande_summer_flowers.summer_flowers.console.set_bed_geometry.main \\
		--kwargs "{'apply': 1}"

A protocol with no bed size cannot turn plants into beds or hectares, and until
this was guarded it did not say so: it fell back to the minimum planting area,
which defaults to one square metre, and reported a plan of 1,688,336 plants as
six beds on 0.0006 hectares with enough land to spare.

The figure is not invented here. It is the most common bed area among that
farm's OWN beds in the register, and only where that farm has beds measured.
Where it has none, the protocol is listed and left alone rather than given the
site-wide number, because a farm whose beds nobody has measured is exactly the
farm whose beds might not be standard.

Only protocols sitting at zero are touched.
"""

import frappe
from frappe.utils import cint, flt

#: What a bed is across this site: 4,789 of them in the register, and 17 of the
#: 19 protocols that state one. Used only to report how unusual a farm is, never
#: to fill a farm in.
SITE_TYPICAL_SQM = 50.0


def main(apply=0, farm=None):
	apply = cint(apply)
	by_farm = _bed_area_by_farm()

	print("bed area per farm, from that farm's own beds:")
	for f in sorted(by_farm):
		a, n = by_farm[f]
		flag = "" if abs(a - SITE_TYPICAL_SQM) < 0.5 else "   <- not the usual 50"
		print("   %-26s %6.1f m2   from %4d bed(s)%s" % (f, a, n, flag))

	rows = frappe.get_all(
		"Crop Protocol",
		filters={"custom_is_summer_flower": 1},
		fields=["name", "farm", "custom_sf_sqm_net_per_bed",
		        "custom_sf_plants_per_bed", "plants_per_sqm"])
	if farm:
		rows = [r for r in rows if r.farm == farm]

	todo, have, nofarm = [], [], []
	for r in rows:
		if flt(r.custom_sf_sqm_net_per_bed) > 0:
			have.append(r)
		elif r.farm in by_farm:
			todo.append((r, by_farm[r.farm][0]))
		else:
			nofarm.append(r)

	print("\n%d protocol(s) already carry a bed area" % len(have))
	print("%d protocol(s) can take their farm's figure" % len(todo))
	print("%d protocol(s) are at a farm with no measured beds — left alone"
	      % len(nofarm))
	for r in nofarm[:8]:
		print("   no beds measured at %-22s %s" % (r.farm, r.name[:46]))
	for r, a in todo[:10]:
		pp = int(round(a * flt(r.plants_per_sqm)))
		print("   set %-46s %5.1f m2 -> %s plants a bed"
		      % (r.name[:46], a, "{:,}".format(pp)))
	if len(todo) > 10:
		print("   ... and %d more" % (len(todo) - 10))

	if not apply:
		print("\n(dry run — pass --kwargs \"{'apply': 1}\" to write)")
		return

	for r, a in todo:
		pp = int(round(a * flt(r.plants_per_sqm)))
		frappe.db.set_value("Crop Protocol", r.name, {
			"custom_sf_sqm_net_per_bed": a,
			"custom_sf_plants_per_bed": pp,
		}, update_modified=False)
		frappe.clear_document_cache("Crop Protocol", r.name)
	frappe.db.commit()
	print("\nwritten to %d protocol(s). They must be re-approved before any plan "
	      "sees it: a plan reads the VERSION, and the version is a snapshot."
	      % len(todo))


def _bed_area_by_farm():
	"""The commonest bed area at each farm, from the beds themselves."""
	rows = frappe.db.sql("""
		select b.farm, round(b.bed_area, 1) a, count(*) n
		from `tabBed` b
		where ifnull(b.bed_area, 0) > 0 and ifnull(b.farm, '') != ''
		group by b.farm, round(b.bed_area, 1)
		order by b.farm, n desc""", as_dict=True)
	out = {}
	for r in rows:
		if r.farm not in out:          # first row per farm is the commonest
			out[r.farm] = (flt(r.a), cint(r.n))
	return out
