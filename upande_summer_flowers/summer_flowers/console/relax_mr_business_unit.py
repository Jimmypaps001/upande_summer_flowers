"""Stop Business Unit being mandatory on Material Request, on this site only.

upande_kaitet ships the field through custom/material_request.json with
sync_on_migrate, so clearing reqd on the Custom Field itself is undone by the
next migrate. A Property Setter is applied over the field when the meta is
built and survives the sync -- which is what Customize Form writes, and is a
site record rather than a line of anybody's code.
"""

import frappe


def run(apply=0, field="custom_business_unit", doctype="Material Request"):
	from frappe.utils import cint
	apply = cint(apply)
	cf = frappe.db.get_value("Custom Field",
	                         {"dt": doctype, "fieldname": field},
	                         ["name", "label", "reqd"], as_dict=True)
	if not cf:
		print("no such field on %s: %s" % (doctype, field))
		return
	print("%s (%s) reqd=%s" % (field, cf.label, cf.reqd))
	existing = frappe.db.get_value("Property Setter",
	                               {"doc_type": "Material Request",
	                                "field_name": field, "property": "reqd"},
	                               ["name", "value"], as_dict=True)
	print("property setter now: %s" % (existing or "none"))
	if existing and existing.value == "0":
		print("   already relaxed")
		return
	if not apply:
		print("\n   (dry run -- pass apply=1 to write it)")
		return
	frappe.make_property_setter({
		"doctype": doctype,
		"doctype_or_field": "DocField",
		"fieldname": field,
		"property": "reqd",
		"value": "0",
		"property_type": "Check",
	}, is_system_generated=False)
	frappe.clear_cache(doctype=doctype)
	frappe.db.commit()
	print("\n   written. mandatory now: %s"
	      % frappe.get_meta(doctype).get_field(field).reqd)
