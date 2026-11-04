"""Leave one Active version per protocol, and put the variety link back."""

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
