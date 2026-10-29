// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

frappe.query_reports["Summer Flower Plan Space"] = {
	filters: [
		{
			fieldname: "production_plan",
			label: __("Production Plan"),
			fieldtype: "Link",
			options: "Summer Flower Production Plan",
			reqd: 1,
		},
	],
};
