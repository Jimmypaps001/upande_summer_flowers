import traceback

import frappe


def main():
    try:
        m = frappe.get_meta("Block")
        print("Block fields:", [(f.fieldname, f.fieldtype, f.options) for f in m.fields
                                if f.fieldtype not in ("Section Break", "Column Break",
                                                       "Tab Break", "HTML", "Table")][:18])
        print("\nhow Karen's blocks are named:",
              frappe.get_all("Block", filters={"farm": "Karen"}, pluck="name")[:4])
        d = frappe.get_doc("Block", frappe.get_all("Block", filters={"farm": "Karen"},
                                                   pluck="name")[0])
        print("one in full:", {k: v for k, v in d.as_dict().items()
                               if v not in (None, 0, 0.0, "", [])
                               and k not in ("modified", "creation", "owner",
                                             "modified_by", "docstatus", "idx", "doctype")})
    except Exception:
        traceback.print_exc()
