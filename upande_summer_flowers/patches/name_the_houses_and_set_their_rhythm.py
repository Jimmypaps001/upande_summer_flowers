"""Name the two houses every protocol was given, and put both on four weeks.

one_place_for_losses_and_houses turned "two suppliers available" into two
rows, but it could not name them: the protocol only ever knew how many.
The farm buys from the same two labs throughout, so they are named here once
rather than typed onto sixty-six rows by hand -- and naming them from a patch
is the only way to do it without saving the protocol, which would put every
approved one back to Draft over a change nobody made to the agronomy.

A row that already names somebody is left exactly as it is: this fills a
blank, it does not overrule an answer.
"""

import frappe
from frappe.utils import cint

# In the order the rows were written: the first house, then the second.
HOUSES = ("Iribov", "Plantech Kenya Limited")
CYCLE_WEEKS = 4


def execute():
	missing = [s for s in HOUSES if not frappe.db.exists("Supplier", s)]
	if missing:
		print("NOT naming houses: no Supplier called %s on this site."
		      % ", ".join(missing))
		return
	rows = frappe.get_all(
		"Summer Flower Protocol Supplier",
		filters={"parenttype": "Crop Protocol",
		         "parentfield": "custom_sf_suppliers"},
		fields=["name", "parent", "idx", "supplier", "cycle_weeks"],
		parent_doctype="Crop Protocol", order_by="parent asc, idx asc")
	named = rhythm = 0
	for r in rows:
		if not r.supplier:
			# idx is one-based, and the houses are in the order they were made.
			house = HOUSES[(cint(r.idx) - 1) % len(HOUSES)]
			frappe.db.set_value("Summer Flower Protocol Supplier", r.name,
			                    "supplier", house, update_modified=False)
			named += 1
		if cint(r.cycle_weeks) != CYCLE_WEEKS:
			frappe.db.set_value("Summer Flower Protocol Supplier", r.name,
			                    "cycle_weeks", CYCLE_WEEKS, update_modified=False)
			rhythm += 1
	frappe.db.commit()
	print("named %d house(s) and put %d onto a delivery every %d weeks"
	      % (named, rhythm, CYCLE_WEEKS))
