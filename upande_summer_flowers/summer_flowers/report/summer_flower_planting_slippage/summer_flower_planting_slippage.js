// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

frappe.query_reports["Summer Flower Planting Slippage"] = {
	filters: [
		{
			fieldname: "production_plan",
			label: __("Production Plan"),
			fieldtype: "Link",
			options: "Summer Flower Production Plan",
		},
		{ fieldname: "farm", label: __("Farm"), fieldtype: "Link", options: "Farm" },
		{ fieldname: "variety", label: __("Variety"), fieldtype: "Link", options: "Item" },
		{ fieldname: "block", label: __("Block"), fieldtype: "Link", options: "Block" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		// Late is the one that costs a harvest week, so it is the one that is red.
		if (column.fieldname === "planting_slip_days" && data) {
			const d = cint(data.planting_slip_days);
			if (d > 0) value = `<span style="color:var(--red-600,#c0392b)">${value}</span>`;
			else if (d < 0) value = `<span style="color:var(--blue-600,#2c7be5)">${value}</span>`;
		}
		return value;
	},
};
