// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Summer Flower Propagation Plan", {
// 	refresh(frm) {
		draw_calendar(frm);

// 	},
// });

function draw_calendar(frm) {
	const field = frm.get_field("calendar_html");
	if (!field || !window.sf_calendar) return;
	const int = (n) => frappe.format(n || 0, { fieldtype: "Int" });

	// Cuttings required rather than plants stuck, because the shortfall is the thing
	// worth seeing on a calendar: which weeks the pool cannot cover.
	const weeks = (frm.doc.weeks || []).map((r) => ({
		date: r.week_start_date,
		value: r.cuttings_required,
		title: `${r.year}-W${String(r.week_no).padStart(2, "0")}: ${
			int(r.plants_to_stick)} ${__("plants")}, ${int(r.cuttings_required)} ${
			__("cuttings")}${r.shortfall ? `, ${__("short")} ${int(r.shortfall)}` : ""}${
			r.notes ? " — " + r.notes : ""}`,
	}));

	const events = [];
	(frm.doc.weeks || []).filter((r) => r.shortfall).forEach((r) => {
		events.push({ date: r.week_start_date, colour: "#c0392b",
			title: `${__("Short by")} ${int(r.shortfall)} ${__("cuttings")}` });
	});
	// The four dates the whole order hangs on. The first is the one that expires.
	const mark = (date, colour, title) => date && events.push({ date, colour, title });
	mark(frm.doc.tc_order_date, "#8e44ad",
		`${__("Order")} ${int(frm.doc.tc_plants_required)} ${__("TC plantlets")}`);
	mark(frm.doc.tc_on_farm_date, "#2980b9", __("Plantlets on farm"));
	mark(frm.doc.first_sticking_date, "#16a085", __("First cutting taken"));
	mark(frm.doc.full_capacity_date, "#27ae60", __("Pool at full cutting capacity"));

	window.sf_calendar(field, {
		weeks, events,
		legend: [{ dot: "#8e44ad", label: __("TC order") },
			{ dot: "#2980b9", label: __("plantlets on farm") },
			{ dot: "#16a085", label: __("first cut") },
			{ dot: "#27ae60", label: __("full capacity") },
			{ dot: "#c0392b", label: __("week short of cuttings") },
			{ label: __("shading: heavier sticking weeks are darker") }],
		empty: __("No sticking weeks yet."),
	});
}
