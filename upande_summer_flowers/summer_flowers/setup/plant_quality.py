# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""The default quality inspection for incoming plant material.

Nothing here is a new inspection engine. ERPNext already carries the whole
chain and it is already in use on this site -- 7,094 Purchase Receipt Items,
8,380 Purchase Invoice Items, 34 Quality Inspections against four templates:

    Purchase Receipt Item / Purchase Invoice Item
        received_qty, qty (accepted), rejected_qty, rejected_warehouse,
        custom_rejection_reason, batch_no, quality_inspection
    Purchase Invoice.update_stock
        so an invoice can BE the receipt, which is how plants often arrive
    Quality Inspection
        reference_type (Purchase Receipt | Purchase Invoice | Stock Entry | ...),
        item_code, sample_size, status (Accepted/Rejected), inspected_by,
        report_date, remarks, readings
    Quality Inspection Reading
        per parameter, numeric with min/max or formula-based acceptance
    Item.inspection_required_before_purchase + Item.quality_inspection_template
        which is what makes an inspection appear by default

So the only thing missing was the plant-specific template and the defaulting.
The 94 Summer Flowers items had inspection_required_before_purchase = 0 and no
template on any of them, which is why plant deliveries arrived uninspected.

Idempotent: safe to re-run, and re-running does not overwrite a threshold
somebody has since corrected.
"""

import frappe

TEMPLATE = "Plant Material Incoming"

# Item groups whose items are plant material when purchased. Roses are
# deliberately not here yet -- they are bought as plants too, but the propagation
# chain this serves is the summer flower one, and switching inspection on for
# 173 rose varieties is a decision for whoever runs that programme.
PLANT_ITEM_GROUPS = ("Summer Flowers",)

# Pests and Diseases and Damages already exist on this site and are reused
# rather than duplicated under a new name.
#
# thresholds: (specification, numeric, min, max, value)
#
# The numbers below are PLACEHOLDERS. Nobody agronomic has signed them off, and
# a rejection threshold that has not been agreed is worse than none at all
# because it rejects real deliveries. They are set wide on purpose and the
# report_missing_thresholds() check names them until someone confirms.
PARAMETERS = [
	("Rooting Percentage", 1, 80.0, 100.0, None),
	("Root Condition", 0, None, None, "White, well branched, no coiling"),
	("Plant Height (cm)", 1, 5.0, 30.0, None),
	("Plant Vigour", 0, None, None, "Turgid, no wilting or yellowing"),
	("Varietal Trueness", 0, None, None, "True to type, correctly labelled"),
	("Pests and Diseases", 1, 0.0, 0.0, None),
	("Damages", 1, 0.0, 5.0, None),
	("Packaging Condition", 0, None, None, "Intact, moist, not overheated"),
]

PLACEHOLDER_THRESHOLDS = ("Rooting Percentage", "Plant Height (cm)", "Damages")


def _parameter(name):
	if frappe.db.exists("Quality Inspection Parameter", name):
		return name
	frappe.get_doc({"doctype": "Quality Inspection Parameter",
	                "parameter": name}).insert(ignore_permissions=True)
	print("quality inspection parameter created: %s" % name)
	return name


def ensure_template():
	"""The template, with a row per parameter. Existing rows are left alone."""
	for spec, _numeric, _lo, _hi, _val in PARAMETERS:
		_parameter(spec)

	if frappe.db.exists("Quality Inspection Template", TEMPLATE):
		doc = frappe.get_doc("Quality Inspection Template", TEMPLATE)
	else:
		doc = frappe.new_doc("Quality Inspection Template")
		doc.quality_inspection_template_name = TEMPLATE

	have = {r.specification for r in (doc.item_quality_inspection_parameter or [])}
	added = 0
	for spec, numeric, lo, hi, val in PARAMETERS:
		if spec in have:
			continue
		row = {"specification": spec, "numeric": numeric}
		if numeric:
			row["min_value"] = lo
			row["max_value"] = hi
		else:
			row["value"] = val
		doc.append("item_quality_inspection_parameter", row)
		added += 1
	if added or doc.is_new():
		doc.save(ignore_permissions=True)
		print("template %s: %d parameter row(s) added, %d total"
		      % (TEMPLATE, added, len(doc.item_quality_inspection_parameter)))
	return doc.name


def apply_to_items(item_groups=None):
	"""Turn the default on for plant items that do not already have one.

	An item that already names a template is left as it is: somebody chose it.
	"""
	groups = list(item_groups or PLANT_ITEM_GROUPS)
	names = frappe.get_all("Item", filters={"item_group": ["in", groups]}, pluck="name")
	changed = 0
	for n in names:
		cur = frappe.db.get_value(
			"Item", n, ["inspection_required_before_purchase", "quality_inspection_template"],
			as_dict=True)
		if cur.quality_inspection_template:
			continue
		frappe.db.set_value("Item", n, {
			"inspection_required_before_purchase": 1,
			"quality_inspection_template": TEMPLATE,
		}, update_modified=False)
		changed += 1
	print("items in %s: %d of %d now require an incoming inspection"
	      % (", ".join(groups), changed, len(names)))
	return changed


def report_missing_thresholds():
	"""Name the thresholds nobody has confirmed, every time this runs."""
	if not frappe.db.exists("Quality Inspection Template", TEMPLATE):
		return
	doc = frappe.get_doc("Quality Inspection Template", TEMPLATE)
	stale = [r.specification for r in doc.item_quality_inspection_parameter
	         if r.specification in PLACEHOLDER_THRESHOLDS]
	if stale:
		print("CONFIRM WITH AN AGRONOMIST before anyone relies on a rejection: "
		      + ", ".join(stale))


@frappe.whitelist()
def install(item_groups=None):
	ensure_template()
	apply_to_items(item_groups)
	report_missing_thresholds()
	return {"template": TEMPLATE}
