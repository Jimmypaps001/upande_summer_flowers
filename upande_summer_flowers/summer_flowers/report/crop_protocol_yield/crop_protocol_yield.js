// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

frappe.query_reports["Crop Protocol Yield"] = {
	filters: [
		{ fieldname: "farm", label: __("Farm"), fieldtype: "Link", options: "Farm" },
		{ fieldname: "variety", label: __("Variety"), fieldtype: "Link", options: "Item" },
		{ fieldname: "approved_only", label: __("Approved Only"), fieldtype: "Check" },
	],
};
