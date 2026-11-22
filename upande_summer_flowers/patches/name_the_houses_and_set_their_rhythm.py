"""Name the two houses every protocol was given, and set each one's rhythm.

one_place_for_losses_and_houses turned "two suppliers available" into two
rows, but it could not name them: the protocol only ever knew how many.
The farm buys from the same two labs throughout, so they are named here once
rather than typed onto sixty-six rows by hand -- and naming them from a patch
is the only way to do it without saving the protocol, which would put every
approved one back to Draft over a change nobody made to the agronomy.

The rhythm is not one figure, because the two kinds of crop are not bought the
same way. A crop raised from our own mother block is stocked with ONE TC order
and then cut from the pool every week: the lab comes once a year, not every
four weeks, and saying four would have the plan order thirteen times for a
block it fills once. A crop planted from what arrives has the lab delivering
on a rhythm, and four weeks is that rhythm here.

Which is which is not guessed: `custom_sf_raised_from_pool` already says so,
derived from the standing stage on the material route, and it is right on
every protocol -- 1 for each TC -> Motherstock -> Plants, 0 for each
TC -> Propagation -> Plants.

A cycle is the GAP between deliveries, so once a year is 52. Both figures are
a starting point, not a rule: the houses table takes a different number for
any crop that wants one, and the planning dashboard can overrule it per plan.

A row that already names somebody is left exactly as it is: this fills a
blank, it does not overrule an answer.
"""

import frappe
from frappe.utils import cint

# In the order the rows were written: the first house, then the second.
HOUSES = ("Iribov", "Plantech Kenya Limited")
# A house delivering on a rhythm comes this often.
CYCLE_DELIVERED = 4
# A mother block is stocked once and cut from all year, so the lab comes once.
CYCLE_ONCE_A_YEAR = 52


def execute():
	missing = [s for s in HOUSES if not frappe.db.exists("Supplier", s)]
	if missing:
		print("NOT naming houses: no Supplier called %s on this site."
		      % ", ".join(missing))
		return
	pool = set(frappe.get_all("Crop Protocol",
	                          filters={"custom_is_summer_flower": 1,
	                                   "custom_sf_raised_from_pool": 1},
	                          pluck="name"))
	rows = frappe.get_all(
		"Summer Flower Protocol Supplier",
		filters={"parenttype": "Crop Protocol",
		         "parentfield": "custom_sf_suppliers"},
		fields=["name", "parent", "idx", "supplier", "cycle_weeks"],
		parent_doctype="Crop Protocol", order_by="parent asc, idx asc")
	named = yearly = rhythm = 0
	for r in rows:
		if not r.supplier:
			# idx is one-based, and the houses are in the order they were made.
			house = HOUSES[(cint(r.idx) - 1) % len(HOUSES)]
			frappe.db.set_value("Summer Flower Protocol Supplier", r.name,
			                    "supplier", house, update_modified=False)
			named += 1
		want = CYCLE_ONCE_A_YEAR if r.parent in pool else CYCLE_DELIVERED
		if cint(r.cycle_weeks) != want:
			frappe.db.set_value("Summer Flower Protocol Supplier", r.name,
			                    "cycle_weeks", want, update_modified=False)
			if want == CYCLE_ONCE_A_YEAR:
				yearly += 1
			else:
				rhythm += 1
	frappe.db.commit()
	print("named %d house(s); %d put on one order a year, %d on a delivery "
	      "every %d weeks" % (named, yearly, rhythm, CYCLE_DELIVERED))
