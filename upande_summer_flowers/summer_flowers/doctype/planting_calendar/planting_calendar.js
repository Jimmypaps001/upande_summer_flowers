// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Planting Calendar", {
// 	refresh(frm) {
		draw_calendar(frm);

// 	},
// });

function draw_calendar(frm) {
	const field = frm.get_field("calendar_html");
	if (!field || !window.sf_calendar) return;
	const int = (n) => frappe.format(n || 0, { fieldtype: "Int" });

	// The flush rows are the weekly figures -- one harvest per row, dated -- and the
	// planting's four lifecycle dates are the markers. Harvested flushes are shown in
	// a different colour from projected ones, because a calendar of expectations and a
	// calendar of what happened are not the same thing.
	const weeks = (frm.doc.flush_projection || []).map((r) => ({
		date: r.harvest_date,
		value: r.is_harvested ? r.actual_stems : r.expected_stems,
		title: `${__("Flush")} ${r.flush_number}: ${
			r.is_harvested
				? `${int(r.actual_stems)} ${__("harvested")} (${__("expected")} ${
					int(r.expected_stems)}, ${__("variance")} ${int(r.variance_stems)})`
				: `${int(r.expected_stems)} ${__("expected")}`}`,
	}));

	const events = [];
	const mark = (date, colour, title) => date && events.push({ date, colour, title });
	mark(frm.doc.sticking_date, "#16a085", __("Stick cuttings"));
	mark(frm.doc.planting_date, "#2980b9", `${__("Plant")} ${int(frm.doc.beds)} ${__("beds")}`);
	mark(frm.doc.pinch_date, "#f39c12", __("Pinch"));
	mark(frm.doc.actual_uproot_date || frm.doc.planned_uproot_date,
		frm.doc.actual_uproot_date ? "#c0392b" : "#95a5a6",
		frm.doc.actual_uproot_date ? __("Uprooted") : __("Planned uproot"));
	(frm.doc.bed_allocation || []).forEach((b) => {
		mark(b.actual_uproot_date, "#c0392b",
			`${__("Bed")} ${b.bed_number || b.bed} ${__("uprooted")}${
				b.uproot_reason ? ": " + b.uproot_reason : ""}`);
	});
	(frm.doc.flush_projection || []).filter((r) => r.is_harvested).forEach((r) => {
		mark(r.harvest_date, "#27ae60",
			`${__("Harvested")} ${int(r.actual_stems)} ${__("stems")}`);
	});

	window.sf_calendar(field, {
		weeks, events,
		legend: [{ dot: "#16a085", label: __("stick") },
			{ dot: "#2980b9", label: __("plant") },
			{ dot: "#f39c12", label: __("pinch") },
			{ dot: "#27ae60", label: __("harvested") },
			{ dot: "#c0392b", label: __("uprooted") },
			{ dot: "#95a5a6", label: __("planned uproot") },
			{ label: __("shading: heavier harvest weeks are darker") }],
		empty: __("No dates on this planting yet."),
	});
}
