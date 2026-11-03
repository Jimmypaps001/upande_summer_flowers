// Stems per Plant, Lifetime is an input, not just a total.
//
// A flush is a share of what a plant gives over its life, and the share is the
// stable thing about it: a variety that hands back 12% of its life in its third
// flush still does that when the life is revised from 13.34 stems to 15. What
// changes then is the stems, in every row at once.
//
// So the table has three ways in and whichever was touched wins -- type the
// life and the rows follow it, type a share and the rows follow that, type a
// row's stems and the life goes back to being the sum of the rows. The same
// rule runs again on the server (crop_protocol.spread_life_over_flushes), which
// is what actually decides; this is here so the figures move while you watch
// rather than only once the form is saved.

frappe.provide("upande_sf");

const TABLE = "custom_sf_flush_schedule";
const LIFE = "custom_sf_stated_stems_per_plant_life";

function rows_of(frm) {
	return frm.doc[TABLE] || [];
}

// Re-reading the shares off the stems every time stops the table holding a
// percentage its own stems contradict.
function reshare(frm) {
	const rows = rows_of(frm);
	const stems = rows.reduce((t, r) => t + flt(r.stems_per_plant), 0);
	let cumulative = 0;
	rows.forEach((r) => {
		if (stems) {
			frappe.model.set_value(r.doctype, r.name, "pct_of_life",
				(flt(r.stems_per_plant) / stems) * 100);
		}
		cumulative += flt(r.pct_of_life);
		frappe.model.set_value(r.doctype, r.name, "cumulative_pct", cumulative);
	});
	return stems;
}

function spread(frm) {
	const rows = rows_of(frm);
	const life = flt(frm.doc[LIFE]);
	const shares = rows.reduce((t, r) => t + flt(r.pct_of_life), 0);
	// A blank life is a blank field, not an instruction to empty the table, and
	// a table stating no shares has nothing to spread a life over.
	if (!rows.length || life <= 0 || shares <= 0) return;
	upande_sf._spreading = true;
	rows.forEach((r) => {
		frappe.model.set_value(r.doctype, r.name, "stems_per_plant",
			(life * flt(r.pct_of_life)) / shares);
	});
	upande_sf._spreading = false;
	reshare(frm);
	frm.refresh_field(TABLE);
}

frappe.ui.form.on("Crop Protocol", {
	[LIFE](frm) {
		if (upande_sf._spreading) return;
		spread(frm);
	},
});

frappe.ui.form.on("Summer Flower Protocol Flush", {
	pct_of_life(frm) {
		if (upande_sf._spreading) return;
		spread(frm);
	},
	stems_per_plant(frm) {
		if (upande_sf._spreading) return;
		upande_sf._spreading = true;
		const stems = reshare(frm);
		// The stems were typed, so the life is their sum.
		if (stems) frm.set_value(LIFE, stems);
		upande_sf._spreading = false;
		frm.refresh_field(TABLE);
	},
	[TABLE + "_remove"](frm) {
		if (upande_sf._spreading) return;
		upande_sf._spreading = true;
		const stems = reshare(frm);
		if (stems) frm.set_value(LIFE, stems);
		upande_sf._spreading = false;
		frm.refresh_field(TABLE);
	},
});
