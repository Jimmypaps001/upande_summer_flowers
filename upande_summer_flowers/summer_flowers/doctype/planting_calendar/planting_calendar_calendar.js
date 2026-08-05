// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt
//
// The list view's Calendar. A planting is not an event on a day, it is a block held
// from the day it goes in the ground to the day it comes out -- 111 weeks for Aster --
// and that occupancy is the scarce thing this whole plan competes for. Read as a list
// you cannot see two plantings overlapping in the same block; as a bar across the
// calendar you can.

frappe.views.calendar["Planting Calendar"] = {
	field_map: {
		start: "planting_date",
		// Actual where it has happened, planned where it has not. A planting uprooted
		// early stops holding its block early, and the calendar should say so rather
		// than keep showing the plan.
		end: "actual_uproot_date",
		id: "name",
		title: "variety",
		status: "calendar_status",
		allDay: 1,
	},
	get_events_method:
		"upande_summer_flowers.summer_flowers.doctype.planting_calendar.planting_calendar.calendar_events",
	filters: [
		{
			fieldtype: "Link",
			fieldname: "farm",
			options: "Farm",
			label: __("Farm"),
		},
		{
			fieldtype: "Link",
			fieldname: "block",
			options: "Block",
			label: __("Block"),
		},
		{
			fieldtype: "Link",
			fieldname: "variety",
			options: "Item",
			label: __("Variety"),
		},
	],
	// Draft and Pending Approval already hold the block -- Planting Calendar refuses a
	// second planting there on that basis -- so they are shown, in a colour that says
	// they are not committed yet.
	style_map: {
		Draft: "warning",
		"Pending Approval": "warning",
		Approved: "info",
		Planted: "success",
		Uprooted: "default",
		Cancelled: "danger",
	},
};
