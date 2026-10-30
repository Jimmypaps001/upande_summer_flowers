# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""What each material stage is, read from the Material Stage records.

A stage used to mean something because its name was written into a tuple here
(BOUGHT_STAGES = ("TC", "Seeds", ...)). It now means what its record says: its
kind, whether it multiplies, whether it is already a plant, the form its
deliveries are lifted as, the item it is bought as and its usual lead time. A
new stage is a new record.

StageSet behaves like the tuples it replaced -- `in`, iteration, `+` -- so the
code that reads them did not have to change, and it is re-read every request.
"""

import frappe
from frappe.utils import cint

DOCTYPE = "Material Stage"

BOUGHT = "Bought"
TAKEN = "Taken from own crop"
MOTHERSTOCK = "Motherstock"
PROPAGATION = "Propagation"
CONDITIONING = "Conditioning"
END = "End"

# What a site starts with (written as records by the add_material_stages patch),
# and what is used before that patch has run. Not consulted once records exist.
SEED = {
	"TC": dict(kind=BOUGHT, lift_form="Tissue Culture", default_lead_weeks=15),
	"Seeds": dict(kind=BOUGHT),
	"Roots": dict(kind=BOUGHT, lift_form="Roots", bought_item_suffix="Roots",
	              default_lead_weeks=52, takes_rooting_losses=1),
	"Tubers": dict(kind=BOUGHT),
	"Budwoods": dict(kind=BOUGHT),
	"Bought-in Plants": dict(kind=BOUGHT, ready_to_plant=1),
	# What each of these is bought as on the season the farm has not got enough
	# of its own standing. Cuttings have no bought form here -- nobody sells an
	# unrooted cutting of these crops -- so the gap on that route is reported and
	# not quietly turned into an order for something else.
	"Roots (Own)": dict(kind=TAKEN, bought_as="Roots"),
	"Cuttings (Own)": dict(kind=TAKEN),
	"Tubers (Own)": dict(kind=TAKEN, bought_as="Tubers"),
	"Motherstock": dict(kind=MOTHERSTOCK, multiplies=1),
	"Rooting": dict(takes_rooting_losses=1, kind=PROPAGATION),
	"Propagation": dict(takes_rooting_losses=1, kind=PROPAGATION),
	"Sprouting": dict(kind=PROPAGATION, multiplies=1, takes_rooting_losses=1),
	"Cooling": dict(takes_rooting_losses=1, kind=CONDITIONING),
	"Plants": dict(kind=END),
}

FIELDS = ("kind", "bought_as", "multiplies", "ready_to_plant", "takes_rooting_losses",
          "lift_form", "bought_item_suffix", "default_lead_weeks")


def all_stages():
	"""{stage: {kind, multiplies, ...}}, cached for the request."""
	cache = getattr(frappe.local, "_sf_stages", None)
	if cache is not None:
		return cache
	cache = {}
	try:
		if frappe.db.table_exists(DOCTYPE):
			for r in frappe.get_all(DOCTYPE, fields=["name", *FIELDS], order_by="idx asc, creation asc"):
				cache[r.name] = dict(r)
	except Exception:
		cache = {}
	if not cache:
		cache = {k: dict(v, name=k) for k, v in SEED.items()}
	frappe.local._sf_stages = cache
	return cache


def clear_cache(*args, **kwargs):
	frappe.local._sf_stages = None


def info(stage):
	return all_stages().get(stage) or {}


def kind(stage):
	return info(stage).get("kind")


def of_kind(*kinds):
	return tuple(s for s, i in all_stages().items() if i.get("kind") in kinds)


def where(flag):
	return tuple(s for s, i in all_stages().items() if cint(i.get(flag)))


def lift_form(stage):
	return info(stage).get("lift_form") or None


def lift_forms():
	"""{stage: lift form} for every stage whose deliveries come in lifts."""
	return {s: i["lift_form"] for s, i in all_stages().items() if i.get("lift_form")}


def bought_as(stage):
	"""The stage an own-crop stage is BOUGHT at when the farm is short of it.

	A crop cut off the farm's own stock is cut off a crop the farm has. Where
	there is not enough of it standing, the difference has to come from a
	supplier -- and it comes as the same material under a name somebody sells:
	own tubers are bought as Tubers. Stated on the stage record rather than
	worked out from the name, because "Roots (Own)" happening to start with
	"Roots" is a spelling, not a fact about the crop.

	Falls back to the name while a site has not been told, so the one site
	already running does not have to be edited before this works. Nothing is
	returned where the bought form does not exist, and then the gap is reported
	rather than filled.
	"""
	named = info(stage).get("bought_as")
	if named:
		return named
	if not stage or not stage.endswith(" (Own)"):
		return None
	plain = stage[: -len(" (Own)")].strip()
	return plain if kind(plain) == BOUGHT else None


def bought_item_suffix(stage):
	return info(stage).get("bought_item_suffix") or None


def default_lead_weeks():
	return {s: cint(i.get("default_lead_weeks")) for s, i in all_stages().items()
	        if cint(i.get("default_lead_weeks"))}


def end_stage():
	ends = of_kind(END)
	return ends[0] if ends else "Plants"


class StageSet:
	"""A live tuple of stage names, chosen by kind or flag."""

	def __init__(self, kinds=(), flag=None, extra=()):
		self.kinds, self.flag, self.extra = tuple(kinds), flag, tuple(extra)

	def _names(self):
		names = list(of_kind(*self.kinds)) if self.kinds else []
		if self.flag:
			names += [s for s in where(self.flag) if s not in names]
		for e in self.extra:
			for s in (e._names() if isinstance(e, StageSet) else ((e,) if isinstance(e, str) else e)):
				if s not in names:
					names.append(s)
		return tuple(names)

	def __contains__(self, item):
		return item in self._names()

	def __iter__(self):
		return iter(self._names())

	def __len__(self):
		return len(self._names())

	def __getitem__(self, i):
		return self._names()[i]

	def __add__(self, other):
		return StageSet(self.kinds, self.flag, self.extra + (other,))

	def __radd__(self, other):
		return StageSet((), None, (other, self))

	def __repr__(self):
		return "StageSet%r" % (self._names(),)
