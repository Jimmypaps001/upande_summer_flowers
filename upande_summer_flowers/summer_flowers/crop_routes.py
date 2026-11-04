# Copyright (c) 2026, James Kiruga and contributors
"""The farm's crop categories: how each variety's material is got, and how it yields."""

import frappe
from frappe import _
from frappe.utils import cint, flt

from upande_summer_flowers.summer_flowers.crop_protocol import (
	PROTOCOL_DOCTYPE,
)

DISTINCT = "Perennial - distinct flushes"
CONTINUOUS = "Perennial - continuous"
SINGLE = "Single flush cycle"

# Route name -> the stages it passes through, in order. The name is for reading;
SEED_ROUTES = {
	"TC to motherstock": ["TC", "Motherstock", "Plants"],
	"TC to propagation": ["TC", "Rooting", "Plants"],
	# Roots bought from a root supplier, not grown from our own tissue culture.
	"Bought as roots": ["Roots", "Rooting", "Plants"],
	"Seed raised": ["Seeds", "Rooting", "Plants"],
	"Bought as plants": ["Bought-in Plants", "Plants"],
	# Own cuttings are a draw from a motherstock, and the motherstock was tissue
	"Own cuttings": ["TC", "Motherstock", "Plants"],
	"Own cuttings, rooted": ["Cuttings (Own)", "Rooting", "Plants"],
	"Own roots, cooled": ["Roots (Own)", "Cooling", "Plants"],
	"Own roots, direct": ["Roots (Own)", "Plants"],
	"Budwood": ["Budwoods", "Rooting", "Plants"],
	# Tubers. Not in the categories document of 14 August -- Dahlia is listed there
	"Own tubers, direct": ["Tubers (Own)", "Plants"],
	"Tubers through cuttings": ["Tubers", "Sprouting", "Rooting", "Plants"],
}


class _Routes:
	"""Route name -> stages, read from the Material Route records."""

	def _all(self):
		cache = getattr(frappe.local, "_sf_routes", None)
		if cache is None:
			cache = {}
			for r in frappe.get_all("Material Route Stage",
			                        filters={"parenttype": "Material Route"},
			                        fields=["parent", "stage"], order_by="parent, idx"):
				cache.setdefault(r.parent, []).append(r.stage)
			frappe.local._sf_routes = cache
		return cache

	def get(self, name, default=None):
		return self._all().get(name, default)

	def __getitem__(self, name):
		return self._all()[name]

	def __contains__(self, name):
		return name in self._all()

	def __iter__(self):
		return iter(self._all())

	def items(self):
		return self._all().items()

	def keys(self):
		return self._all().keys()


ROUTES = _Routes()

# (route, cycle, [varieties]). Written out as the document has them, including the
CATALOGUE = [
	("TC to motherstock", DISTINCT, [
		"Aster (Novi Belgii Grp) Dark Milka",
		"Aster (Novi Belgii Grp) Double Date White",
		"Aster (Novi Belgii Grp) Teeny Tiny Blue",
		"Aster (Novi belgii Grp) Teeny Tiny Pink",
		"Aster (Novi-belgii Grp) Double Date Milka",
		"Aster (Novi-belgii Grp) Double Date Pink",
		"Aster (Novi-belgii Grp) 'Flash'",
		# Not in the categories document of 14 August, but grown at Carzan Ks with a
		"Aster Double Date Dusk",
		"Aster Teeny Tiny White",
		"Phlox (Paniculata Grp) Pink Eyes",
		"Phlox (Paniculata Grp) Violet Eyes",
		"Phlox Paniculata Grp Icecap",
		"Solidago Carzan Glory",
		"Solidago Carzan Moonlight",
		"Solidago Carzan Taramba",
		"Bouvardia",
	]),
	("TC to motherstock", CONTINUOUS, [
		"Scabiosa atropurpurea Bon Bon Marachino Cherry Scoop",
		"Scabiosa atropurpurea Dark Cherry Scoop",
		"Scabiosa atropurpurea Focal Scoop Bicolor Pink",
		"Scabiosa atropurpurea French Vanilla Bon Bon Scoop",
		"Scabiosa atropurpurea Saura",
	]),
	# Delphinium and Helleborus only. The Eryngiums are listed under this route AND
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
	("TC to propagation", CONTINUOUS, [
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
	("Own cuttings, rooted", DISTINCT, [
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
	# Dahlia produces from roughly 9-12 weeks after the tuber goes in and then keeps
	("Tubers through cuttings", CONTINUOUS, [
		"Dahlia",
	]),
]

# The stage material is bought at, per route. Only the first stage is a purchase
CATEGORY_FARMS = {
	("TC to motherstock", DISTINCT): ("Carzan Ks", "Kariki Juja", "Carzan St"),
	("TC to motherstock", CONTINUOUS): ("Carzan Mr",),
	("TC to propagation", CONTINUOUS): ("Kariki Naivasha", "Carzan Mr", "Kudenga", "Bondet"),
	("Seed raised", DISTINCT): ("Kariki Naivasha", "Carzan Mr"),
	("Seed raised", SINGLE): (),
	("Bought as plants", CONTINUOUS): ("Carzan Mr", "Kudenga", "Carzan Ks"),
	("Bought as plants", DISTINCT): ("Kariki Naivasha", "Kudenga"),
	("Bought as plants", SINGLE): (),
	("Own cuttings, rooted", DISTINCT): ("Carzan Ks", "Carzan St", "Bondet",
	                                     "Kariki Naivasha", "Kariki Juja"),
	("Own roots, cooled", CONTINUOUS): ("Bondet", "Kudenga"),
	("Own roots, cooled", DISTINCT): ("Kudenga",),
	("Own roots, direct", SINGLE): ("Carzan St", "Carzan Ks"),
	("Own roots, direct", CONTINUOUS): ("Kariki Naivasha",),
	("Tubers through cuttings", CONTINUOUS): ("Carzan Ks",),
}


def farms_for(route, cycle):
	return CATEGORY_FARMS.get((route, cycle), ())


def grows_here(farm, route, cycle):
	want = (farm or "").strip().lower()
	return any(want == f.strip().lower() for f in farms_for(route, cycle))


SECOND_WAY = {
	v: ("Bought as roots", 35.0)
	for v in ("Eryngium Aquarius Questar", "Eryngium Magnetar Questar",
	          "Eryngium Orion Questar", "Eryngium planum Gemini Questar",
	          "Eryngium Scorpius Questar", "Eryngium Sirius Questar",
	          "Eryngium Supernova Questar")
}


def second_way():
	from upande_summer_flowers.summer_flowers.settings import second_way as _s

	return _s(SECOND_WAY)



def _lookup():
	"""variety -> (route, cycle), and the varieties claimed by more than one entry."""
	seen, clash = {}, {}
	for route, cycle, varieties in CATALOGUE:
		for v in varieties:
			if v in seen and seen[v] != (route, cycle):
				clash.setdefault(v, [seen[v]]).append((route, cycle))
			seen[v] = (route, cycle)
	return seen, clash


#: How far ahead each bought stage has to be ordered. Unlike the weeks and the


def lead_weeks():
	"""Supplier lead per bought stage: each Material Stage's default lead weeks."""
	from upande_summer_flowers.summer_flowers import stages
	from upande_summer_flowers.summer_flowers.settings import lead_weeks as _l

	return _l(stages.default_lead_weeks())


def stages_for(route):
	"""The ordered stage rows for a route, ready to append to a protocol."""
	# The stage a route buys is its first, when that stage is a bought one.
	from upande_summer_flowers.summer_flowers import stages

	first = (ROUTES.get(route) or [None])[0]
	bought = first if stages.kind(first) == stages.BOUGHT else None
	return [
		{"stage": stage, "weeks": 0, "loss_pct": 0, "yields_per_unit": 1,
		 "is_purchase": 1 if stage == bought else 0,
		 "lead_weeks": lead_weeks().get(stage, 0)}
		for stage in ROUTES[route]
	]


@frappe.whitelist()
def classify(dry_run=1, farm=None):
	"""Write the route and the cycle onto every Crop Protocol we have a category for."""
	dry_run = int(dry_run or 0)
	by_variety, clash = _lookup()

	f = {}
	if farm:
		f["farm"] = farm
	protocols = frappe.get_all(PROTOCOL_DOCTYPE, filters=f,
	                           fields=["name", "variety", "farm"])
	# Whether a protocol already carries a route is read from the stages, not from
	routed = {r.parent for r in frappe.get_all(
		"Crop Material Stage",
		filters={"parenttype": PROTOCOL_DOCTYPE,
		         "parentfield": "custom_sf_material_route"},
		fields=["parent"], limit_page_length=0)}

	done, skipped, unmatched = [], [], []
	for p in protocols:
		hit = by_variety.get(p.variety)
		if not hit:
			unmatched.append(p.variety)
			continue
		if p.name in routed:
			skipped.append(p.name)
			continue
		route, cycle = hit
		if not dry_run:
			doc = frappe.get_doc(PROTOCOL_DOCTYPE, p.name)
			doc.set("custom_sf_material_route", [])
			for row in stages_for(route):
				doc.append("custom_sf_material_route", row)
			doc.custom_sf_growing_cycle = cycle
			doc.flags.ignore_permissions = True
			doc.save()
		done.append("%s: %s | %s" % (p.name, " -> ".join(ROUTES[route]), cycle))

	# Varieties the catalogue knows that this site has no protocol for. Worth
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
	"""Readable version of classify(), for the console."""
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


# The farms named in the source document, as spelled there.
FARMS = ["Carzan Ks", "Carzan Mr", "Carzan St", "Kariki Juja", "Kariki Naivasha",
         "Kudenga", "Bondet"]

ITEM_GROUP = "Summer Flowers"


def _ensure_item(variety, dry_run):
	"""The variety as an Item, because that is what a protocol links to."""
	if frappe.db.exists("Item", variety):
		return "have"
	hit = frappe.db.get_value("Item", {"item_name": variety}, "name")
	if hit:
		return "have"
	if dry_run:
		return "would create"
	group = (ITEM_GROUP if frappe.db.exists("Item Group", ITEM_GROUP)
	         else frappe.db.get_value("Item Group", {"is_group": 0}, "name"))
	doc = frappe.new_doc("Item")
	doc.item_code = variety
	doc.item_name = variety
	doc.item_group = group
	doc.stock_uom = "Nos" if frappe.db.exists("UOM", "Nos") else \
		frappe.db.get_value("UOM", {}, "name")
	# Matched to the summer flower items this site already has. A site rule refuses
	doc.is_stock_item = 1
	doc.is_purchase_item = 1
	doc.is_sales_item = 1
	doc.flags.ignore_permissions = True
	doc.flags.ignore_mandatory = True
	doc.insert()
	return "created"


@frappe.whitelist()
def provision(farm, dry_run=1, only_route=None):
	"""Create the varieties and their protocols for one farm."""
	dry_run = int(dry_run or 0)
	if not frappe.db.exists("Farm", farm):
		frappe.throw(_("No farm called {0} on this site.").format(frappe.bold(farm)))

	by_variety, _clash = _lookup()
	items, protocols, existing = {"have": 0, "created": 0, "would create": 0}, [], []

	for variety, (route, cycle) in sorted(by_variety.items()):
		if only_route and route != only_route:
			continue
		if not grows_here(farm, route, cycle):
			continue
		items[_ensure_item(variety, dry_run)] += 1
		name = "%s-%s" % (variety, farm)
		if frappe.db.exists(PROTOCOL_DOCTYPE, name):
			existing.append(name)
			continue
		if not dry_run:
			doc = frappe.new_doc(PROTOCOL_DOCTYPE)
			doc.variety = variety
			doc.variety_item = variety
			doc.farm = farm
			doc.crop_type = "Summer Flowers"
			doc.custom_is_summer_flower = 1
			doc.custom_sf_crop_class = "Summer Flower"
			doc.custom_sf_growing_cycle = cycle
			for row in stages_for(route):
				doc.append("custom_sf_material_route", row)
			doc.custom_sf_change_reason = _("Created from the crop categories of "
			                                "14 August 2026.")
			doc.flags.ignore_permissions = True
			doc.flags.ignore_mandatory = True
			doc.insert()
		protocols.append("%s | %s | %s" % (name, " -> ".join(ROUTES[route]), cycle))

	if not dry_run:
		frappe.db.commit()
	return {"farm": farm, "dry_run": bool(dry_run), "items": items,
	        "protocols_created": protocols, "protocols_already_there": existing}


def provision_report(farm, dry_run=1, only_route=None):
	"""Readable provision(), for the console."""
	r = provision(farm=farm, dry_run=dry_run, only_route=only_route)
	print("%s -- %s\n" % (r["farm"], "DRY RUN, nothing written" if r["dry_run"] else "WRITTEN"))
	print("items: %s" % r["items"])
	print("\nprotocols (%d):" % len(r["protocols_created"]))
	for x in r["protocols_created"][:80]:
		print("   ", x)
	if r["protocols_already_there"]:
		print("\nalready there (%d)" % len(r["protocols_already_there"]))
	return r


# The shape of a season is a fact about a crop, so it is not written here. A crop
def _weights(raw):
	out = []
	for part in str(raw or "").replace(";", ",").split(","):
		part = part.strip()
		if not part:
			continue
		try:
			out.append(float(part))
		except ValueError:
			frappe.throw(_("{0} is not a number. A season curve is a list of weights, "
			               "like 40,55,70,85,95.").format(frappe.bold(part)))
	return out


def season_shape_for(doc):
	"""The build-up and tail-off weights for this crop: its own, or the farm's."""
	build = _weights(doc.get("custom_sf_season_curve_build"))
	tail = _weights(doc.get("custom_sf_season_curve_tail"))
	if not build and not tail:
		s = frappe.get_cached_doc("Summer Flower Settings")
		build = _weights(s.get("season_curve_build"))
		tail = _weights(s.get("season_curve_tail"))
	return {"build": build, "tail": tail, "peak": 100.0}


def season_weights(weeks, shape):
	"""Relative weight per producing week, for `weeks` weeks."""
	build = list(shape.get("build") or [])
	tail = list(shape.get("tail") or [])
	peak = shape.get("peak", 100.0)
	if not build and not tail:
		# Nothing stated: the season is flat, which is at least a shape someone chose
		return [peak] * weeks
	if weeks <= 1:
		return [peak] * max(weeks, 0)

	full = build + [peak] + tail
	if weeks >= len(full):
		middle = [peak] * (weeks - len(build) - len(tail))
		return build + middle + tail
	# Shorter than the shape: sample it evenly rather than cut an end off.
	last = len(full) - 1
	return [full[int(round(i * last / (weeks - 1)))] for i in range(weeks)]


@frappe.whitelist()
def spread_season(protocol, dry_run=1, shape=None):
	"""Allocate a continuous crop's whole life across its producing weeks."""
	from upande_summer_flowers.summer_flowers.crop_protocol import PROTOCOL_DOCTYPE

	dry_run = int(dry_run or 0)
	doc = frappe.get_doc(PROTOCOL_DOCTYPE, protocol)
	weeks = cint(doc.get("custom_sf_productive_weeks"))
	# Where the picking starts. Stated outright on a continuous crop; on a crop that
	first = (cint(doc.get("custom_sf_weeks_planting_to_first_cut"))
	         or cint(doc.get("custom_sf_first_harvest_offset_weeks")))
	life = flt(doc.get("custom_sf_stated_stems_per_plant_life")) or \
		flt(doc.get("total_stems_per_plant_life"))
	if not weeks or not life:
		frappe.throw(_("{0} needs productive weeks and a life yield before its season "
		               "can be shared out.").format(frappe.bold(protocol)))

	weights = season_weights(weeks, shape or season_shape_for(doc))
	total = sum(weights) or 1
	rows = []
	for i, w in enumerate(weights):
		rows.append({
			"flush_number": i + 1,
			"weeks_from_planting": first + i,
			"pct_of_life": w / total * 100.0,
			"stems_per_plant": life * w / total,
		})
	if not dry_run:
		doc.set("custom_sf_flush_schedule", [])
		for r in rows:
			doc.append("custom_sf_flush_schedule", r)
		doc.flags.ignore_permissions = True
		doc.flags.ignore_mandatory = True
		frappe.flags.sf_protocol_move = True
		doc.save()
		frappe.db.commit()
		frappe.flags.sf_protocol_move = False
	return {"protocol": protocol, "weeks": weeks, "life": life,
	        "dry_run": bool(dry_run), "rows": rows}
