# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""The farm's crop categories: how each variety's material is got, and how it yields.

Two things decide how a crop is planned, and they are independent of each other.

The ROUTE is how plant material becomes a plant in the ground. Nine of them are
written down, and they are nine paths through one short list of stages -- so they
are held as ordered stages rather than as nine names. A tenth route is then data.

The CYCLE is how the plant yields once it is there. Distinct flushes are scheduled
one at a time, which is the only thing the planner can currently do. Continuous
produces every week across a window. A single flush is cut once and the block comes
out. The last two are recorded here and not yet acted on: naming them is the point
of this pass.

Source: Crop Categories, 14 August 2026.
"""

import frappe
from frappe import _

DISTINCT = "Perennial - distinct flushes"
CONTINUOUS = "Perennial - continuous"
SINGLE = "Single flush cycle"

# Route name -> the stages it passes through, in order. The name is for reading;
# the stages are what anything downstream measures.
ROUTES = {
	"TC to motherstock": ["TC", "Motherstock", "Plants"],
	"TC to propagation": ["TC", "Propagation", "Plants"],
	"TC through roots": ["TC", "Roots", "Propagation", "Plants"],
	"Seed raised": ["Seeds", "Propagation", "Plants"],
	"Bought as plants": ["Bought-in Plants", "Plants"],
	"Own cuttings": ["Cuttings (Own)", "Propagation", "Plants"],
	"Own roots, cooled": ["Roots (Own)", "Cooling", "Plants"],
	"Own roots, direct": ["Roots (Own)", "Plants"],
	"Budwood": ["Budwoods", "Propagation", "Plants"],
}

# (route, cycle, [varieties]). Written out as the document has them, including the
# spellings, so a name that does not match a site's Item is visible as a miss
# rather than being quietly approximated.
CATALOGUE = [
	("TC to motherstock", DISTINCT, [
		"Aster (Novi Belgii Grp) Dark Milka",
		"Aster (Novi Belgii Grp) Double Date White",
		"Aster (Novi Belgii Grp) Teeny Tiny Blue",
		"Aster (Novi belgii Grp) Teeny Tiny Pink",
		"Aster (Novi-belgii Grp) Double Date Milka",
		"Aster (Novi-belgii Grp) Double Date Pink",
		"Aster (Novi-belgii Grp) 'Flash'",
		"Aster Teeny Tiny White",
		"Phlox (Paniculata Grp) Pink Eyes",
		"Phlox (Paniculata Grp) Violet Eyes",
		"Phlox Paniculata Grp Icecap",
		"Solidago Carzan Glory",
		"Solidago Carzan Moonlight",
		"Solidago Carzan Taramba",
	]),
	("TC to motherstock", CONTINUOUS, [
		"Scabiosa atropurpurea Bon Bon Marachino Cherry Scoop",
		"Scabiosa atropurpurea Dark Cherry Scoop",
		"Scabiosa atropurpurea Focal Scoop Bicolor Pink",
		"Scabiosa atropurpurea French Vanilla Bon Bon Scoop",
		"Scabiosa atropurpurea Saura",
	]),
	# Delphinium and Helleborus only. The Eryngiums are listed under this route AND
	# under "TC through roots" in the source document; they are resolved below
	# rather than being claimed by whichever entry ran last.
	("TC to propagation", CONTINUOUS, [
		"Delphinium Andes Azure",
		"Delphinium Andes White",
		"Delphinium Paramo Azul",
		"Delphinium Paramo Black Velvet",
		"Delphinium Paramo Blanco",
		"Helleborus Bella Belles",
		"Helleborus Classic Grace",
		"Helleborus Classic One",
	]),
	("TC through roots", CONTINUOUS, [
		"Eryngium Aquarius Questar",
		"Eryngium Magnetar Questar",
		"Eryngium Orion Questar",
		"Eryngium planum Gemini Questar",
		"Eryngium Scorpius Questar",
		"Eryngium Sirius Questar",
		"Eryngium Supernova Questar",
	]),
	("Seed raised", DISTINCT, [
		"Delphinium (Elatum Grp) Guardian Blue",
		"Delphinium (Elatum Grp) Triton Lavender",
		"Matricaria",
	]),
	("Seed raised", SINGLE, [
		"Bupleurum rotundifolium",
		"Daucas",
	]),
	("Bought as plants", CONTINUOUS, [
		"Craspedia globosa Paintball Pop",
		"Limonium 'Skylight'",
		"Limonium Safora Dark Blue",
	]),
	("Bought as plants", DISTINCT, [
		"Gypsophila paniculata Million Daisy",
		"Gypsophila paniculata Xlence",
	]),
	("Bought as plants", SINGLE, [
		"Limonium sinensis Scarlet Diamond",
		"Limonium sinensis Anouchka Diamond",
	]),
	("Own cuttings", DISTINCT, [
		"Hypericum androsaemum Sisu Charm",
		"Hypericum androsaemum Sisu Flirt",
		"Hypericum androsaemum Sisu Red",
		"Hypericum androsaemum Sisu Twinkle",
		"Hypericum androsaemum Tomato Flair",
		"Clematis (Diversifolia Grp) Blue Pirouette",
		"Clematis (Integrifolia Grp) Amazing Geneva",
		"Clematis Amazing Havana",
		"Clematis Diversifolia Grp Kyiv",
	]),
	("Own roots, cooled", CONTINUOUS, [
		"Astrantia major Billion Stars XL",
		"Astrantia major 'Star of Africa'",
		"Astrantia major Star of Desire",
		"Astrantia major 'Star Of Love'",
	]),
	("Own roots, cooled", DISTINCT, [
		"Astilbe chinensis 'Vision in Pink'",
	]),
	("Own roots, direct", SINGLE, [
		"Sanguisorba officinalis Black Dream Select",
		"Sanguisorba officinalis 'Red Dream'",
	]),
	("Own roots, direct", CONTINUOUS, [
		"Agapanthus 'Gletsjer'",
	]),
	("Budwood", CONTINUOUS, [
		"Rosa large flowered Confidential",
		"Rosa large flowered Esperance",
		"Rosa large flowered Helene",
		"Rosa large flowered Madam red",
		"Rosa large flowered Pink Athena",
		"Rosa large flowered Snowstorm+",
		"Rosa small flowered Candid Prophyta",
		"Rosa small flowered Menta",
	]),
]

# The stage material is bought at, per route. Only the first stage is a purchase
# unless the farm says otherwise; own cuttings and own roots are taken, not bought,
# so nothing on those routes is marked bought.
PURCHASED_AT = {
	"TC to motherstock": "TC",
	"TC to propagation": "TC",
	"TC through roots": "TC",
	"Seed raised": "Seeds",
	"Bought as plants": "Bought-in Plants",
	"Budwood": "Budwoods",
}


def _lookup():
	"""variety -> (route, cycle), and the varieties claimed by more than one entry."""
	seen, clash = {}, {}
	for route, cycle, varieties in CATALOGUE:
		for v in varieties:
			if v in seen and seen[v] != (route, cycle):
				clash.setdefault(v, [seen[v]]).append((route, cycle))
			seen[v] = (route, cycle)
	return seen, clash


def stages_for(route):
	"""The ordered stage rows for a route, ready to append to a protocol."""
	bought = PURCHASED_AT.get(route)
	return [
		{"stage": stage, "weeks": 0, "loss_pct": 0, "yields_per_unit": 1,
		 "is_purchase": 1 if stage == bought else 0}
		for stage in ROUTES[route]
	]


@frappe.whitelist()
def classify(dry_run=1, farm=None):
	"""Write the route and the cycle onto every Crop Protocol we have a category for.

	Matches on the protocol's variety. Weeks and losses are left at zero: this pass
	records WHICH route a crop takes, not how long each stage of it lasts -- those
	are per farm and per crop and have to be typed by someone who knows them.

	Nothing is overwritten. A protocol that already carries a route is left alone
	and reported, so running this twice is safe and so is running it after someone
	has corrected one by hand.
	"""
	dry_run = int(dry_run or 0)
	by_variety, clash = _lookup()

	f = {}
	if farm:
		f["farm"] = farm
	protocols = frappe.get_all("Crop Protocol", filters=f,
	                           fields=["name", "variety", "farm",
	                                   "custom_sf_route_summary"])

	done, skipped, unmatched = [], [], []
	for p in protocols:
		hit = by_variety.get(p.variety)
		if not hit:
			unmatched.append(p.variety)
			continue
		if p.custom_sf_route_summary:
			skipped.append("%s (%s)" % (p.name, p.custom_sf_route_summary))
			continue
		route, cycle = hit
		if not dry_run:
			doc = frappe.get_doc("Crop Protocol", p.name)
			doc.set("custom_sf_material_route", [])
			for row in stages_for(route):
				doc.append("custom_sf_material_route", row)
			doc.custom_sf_growing_cycle = cycle
			doc.flags.ignore_permissions = True
			doc.save()
		done.append("%s: %s | %s" % (p.name, " -> ".join(ROUTES[route]), cycle))

	# Varieties the catalogue knows that this site has no protocol for. Worth
	# saying: it is the list of protocols still to be written, not an error.
	have = {p.variety for p in protocols}
	no_protocol = sorted(v for v in by_variety if v not in have)

	report = {
		"classified": done, "already_had_one": skipped,
		"protocol_variety_not_in_catalogue": sorted(set(unmatched)),
		"catalogue_variety_without_protocol": no_protocol,
		"conflicts": {v: c for v, c in clash.items()},
		"dry_run": bool(dry_run),
	}
	if not dry_run:
		frappe.db.commit()
	return report


def report(dry_run=1):
	"""Readable version of classify(), for the console.

	    bench --site SITE execute \
	        upande_summer_flowers.summer_flowers.crop_routes.report
	    bench --site SITE execute \
	        upande_summer_flowers.summer_flowers.crop_routes.report --kwargs "{'dry_run': 0}"
	"""
	r = classify(dry_run=dry_run)
	print("DRY RUN -- nothing written\n" if r["dry_run"] else "WRITTEN\n")
	for key, label in (
		("classified", "classified"),
		("already_had_one", "left alone, already carried a route"),
		("protocol_variety_not_in_catalogue", "protocols whose variety is not in the catalogue"),
		("catalogue_variety_without_protocol", "catalogue varieties with no protocol here"),
	):
		rows = r[key]
		print("%s (%d):" % (label, len(rows)))
		for x in rows[:40]:
			print("   ", x)
		if len(rows) > 40:
			print("    ... and %d more" % (len(rows) - 40))
		print()
	if r["conflicts"]:
		print("CONFLICTS -- listed under more than one route in the source document:")
		for v, c in r["conflicts"].items():
			print("   %s: %s" % (v, c))
	return r
