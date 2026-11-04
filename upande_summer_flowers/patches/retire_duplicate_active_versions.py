"""Leave one Active version per protocol, and put the variety link back.

The October item merge repointed Crop Protocols onto numbered items. Versions
snapshotted before it kept the variety NAME, so the lookup that retires the
version in force -- keyed on (variety, farm) -- stopped seeing them. Approving
a new version then superseded nothing, and two rows were left Active with
is_current = 1. Which of them a plan reported was down to row order:

    Aster (Novi Belgii Grp) Dark Milka-Carzan Ks
        v2  current=1  variety=15557
        v1  current=1  variety='Aster (Novi Belgii Grp) Dark Milka'  <- reported

A second shape reaches the same place without the merge: one protocol whose
farm is spelled two ways across its versions, 3359-Bondet-v2 beside
3359-Kariki Nanyuki-v1.

The repair: within one protocol at one farm, the newest by version -- then by
the date it takes effect -- stays Active; the rest are superseded, pointed at
it, and closed the day before it starts.

The farm is part of that key and has to be. Some rows carry a crop_protocol
belonging to a different farm (Kariki Naivasha-v1 points at the Kariki Juja
protocol), and grouping on the protocol alone retired Naivasha's live version
along with Juja's.

The stale variety links are left alone. Repointing them from the protocol
sounds obvious and is not: the protocol's own farm often disagrees with the
version's, so the link cannot be trusted to say what the variety is. The code
that approves a version now asks by the protocol as well as by (variety, farm),
so a name left behind no longer hides a version from it.

Nothing is deleted. A superseded version is still readable, still the thing a
plan pinned to it was built on.
"""

import frappe
from frappe.utils import add_days, cint, getdate


def execute():
	retired = _retire_all_but_newest()
	print("Crop Protocol Version: duplicate actives retired: %d" % retired)


def _retire_all_but_newest():
	rows = frappe.get_all(
		"Crop Protocol Version",
		filters={"version_status": "Active"},
		fields=["name", "version", "effective_from", "crop_protocol",
		        "variety", "farm"])
	groups = {}
	for r in rows:
		# The protocol AND the farm. A protocol is approved for a variety at a
		# farm, so two farms are never the same line -- and a mislinked row
		# would otherwise be grouped with them. Dropping the farm here retired
		# Kariki Naivasha's live version alongside Kariki Juja's.
		key = (r.crop_protocol or r.variety, r.farm)
		groups.setdefault(key, []).append(r)

	n = 0
	for key, group in groups.items():
		if len(group) < 2:
			continue
		group.sort(key=lambda r: (cint(r.version), str(r.effective_from or "")),
		           reverse=True)
		keep = group[0]
		print("  %s @ %s: keeping %s (v%s)" % (key[0], key[1], keep.name,
		                                      keep.version))
		for r in group[1:]:
			frappe.db.set_value("Crop Protocol Version", r.name, {
				"version_status": "Superseded",
				"is_current": 0,
				"superseded_by": keep.name,
				"effective_to": (add_days(getdate(keep.effective_from), -1)
				                 if keep.effective_from else None),
			}, update_modified=False)
			print("    retired %s (v%s)" % (r.name, r.version))
			n += 1
	return n
