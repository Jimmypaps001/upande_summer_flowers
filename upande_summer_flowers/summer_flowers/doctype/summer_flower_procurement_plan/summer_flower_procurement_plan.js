// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

// An approved procurement plan stops being a decision and becomes a set of
// dates. What it buys, whether that meets the demand, when the material lands
// and when the first stems come off are the questions anybody asks of one, and
// answering them meant opening the production plan, the propagation plan and
// the planting calendar in three tabs.

// Choosing who raises the material. Offered on a draft, because the buying is
// already settled by the time this plan exists -- the material is bought the
// same way whichever unit raises it. What this decides is where it goes, and
// the answer comes from the troughs rather than from a share typed in.
function allocate_units(frm) {
	frappe.call({
		doc: frm.doc,
		method: "propose_allocation",
		freeze: true,
		freeze_message: __("Working the season through each unit's troughs..."),
	}).then((r) => {
		if (!r.message) return;
		const m = r.message;
		frm.reload_doc();
		frappe.msgprint({
			title: __("Allocated"),
			indicator: m.to_buy ? "orange" : "green",
			message: __("{0} units take {1} of {2} plants. {3} must be bought in as plants.",
				[m.units, frappe.format(m.allocated, {fieldtype: "Int"}),
				 frappe.format(m.season, {fieldtype: "Int"}),
				 frappe.format(m.to_buy, {fieldtype: "Int"})]),
		});
	});
}

// A share is only meaningful once it is a share of something. The pool is the
// production plan's weekly plantings summed -- the same figure propose_allocation
// divides -- fetched once per form so typing a percentage can answer in plants
// immediately rather than after a round trip per keystroke.
function load_pool(frm) {
	if (!frm.doc.production_plan) { frm._alloc_pool = null; return Promise.resolve(); }
	return frm.call({ doc: frm.doc, method: "allocation_pool" }).then((r) => {
		frm._alloc_pool = (r && r.message) || null;
		paint_allocation(frm);
	});
}

// Plants from a share, and the share back from plants, so the two columns agree
// whichever one was typed in. Rounded to whole plants because half a plant is
// not a thing anyone can stick.
function alloc_plants(frm, row) {
	const pool = frm._alloc_pool && frm._alloc_pool.season_plants;
	if (!pool) return null;
	return Math.round((flt(row.share_pct) * pool) / 100);
}

function paint_allocation(frm) {
	const grid = frm.fields_dict.propagation_allocation;
	if (!grid || !grid.grid) return;
	const pool = frm._alloc_pool && frm._alloc_pool.season_plants;
	const rows = frm.doc.propagation_allocation || [];
	const pct = rows.reduce((a, r) => a + flt(r.share_pct), 0);
	const plants = rows.reduce((a, r) => a + cint(r.plants), 0);
	if (!pool) { grid.$wrapper.find(".sfp-alloc-note").remove(); return; }
	const left = pool - plants;
	const msg = __("{0} of {1} plants allocated ({2}%). {3}", [
		format_number(plants, null, 0), format_number(pool, null, 0),
		pct.toFixed(2),
		left > 0 ? __("{0} still to buy in as plants.", [format_number(left, null, 0)])
			: (left < 0 ? __("Over-allocated by {0}.", [format_number(-left, null, 0)])
				: __("Fully allocated.")),
	]);
	grid.$wrapper.find(".sfp-alloc-note").remove();
	grid.$wrapper.prepend(
		`<p class="sfp-alloc-note text-muted" style="font-size:.8rem;margin:0 0 6px"${
			left < 0 ? ' data-over="1"' : ""}>${frappe.utils.escape_html(msg)}</p>`);
}

frappe.ui.form.on("Summer Flower Propagation Allocation", {
	// Typing a share fills the plants beside it. The unit's own columns --
	// planted, surplus, troughs -- stay blank until the troughs are actually
	// worked through, because only "Choose Propagation Units" knows those.
	share_pct(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const n = alloc_plants(frm, row);
		if (n === null) {
			frappe.show_alert({ message: __("No production plan, so a share has nothing to be a share of."), indicator: "orange" });
			return;
		}
		frappe.model.set_value(cdt, cdn, "plants", n).then(() => paint_allocation(frm));
	},
	// And the other way: an absolute number sets the share it represents, so a
	// row entered either way reads the same.
	plants(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const pool = frm._alloc_pool && frm._alloc_pool.season_plants;
		if (!pool) return;
		const pct = Math.round((cint(row.plants) * 10000) / pool) / 100;
		if (flt(row.share_pct).toFixed(2) !== pct.toFixed(2)) {
			frappe.model.set_value(cdt, cdn, "share_pct", pct)
				.then(() => paint_allocation(frm));
		} else {
			paint_allocation(frm);
		}
	},
	// The running total has to follow every way the table can change, not only
	// the two fields above: a row removed, a unit swapped, or a row added by
	// "Choose Propagation Units" all move it.
	propagation_allocation_remove(frm) { paint_allocation(frm); },
	propagation_allocation_add(frm) { paint_allocation(frm); },
	propagation_unit(frm) { paint_allocation(frm); },
});

frappe.ui.form.on("Summer Flower Procurement Plan", {
	refresh(frm) {
		load_pool(frm);
		if (frm.doc.docstatus === 0 && frm.doc.production_plan) {
			frm.add_custom_button(__("Choose Propagation Units"),
				() => allocate_units(frm));
		}

		if (frm.doc.docstatus !== 1) {
			frm.get_field("overview_html").$wrapper.empty();
			return;
		}
		frm.call({ doc: frm.doc, method: "overview_for_form" })
			.then((r) => sf_proc_overview(frm, r.message));
	},

	production_plan(frm) {
		load_pool(frm);
	},
});

function sf_proc_overview(frm, o) {
	const el = frm.get_field("overview_html").$wrapper;
	if (!o) { el.empty(); return; }
	const esc = frappe.utils.escape_html;
	const int = (n) => format_number(n || 0, null, 0);
	const day = (d) => (d ? frappe.datetime.str_to_user(d) : "—");

	const tile = (k, v, m, c) =>
		`<div class="sfp-tile"><div class="k">${esc(k)}</div>` +
		`<div class="v"${c ? ` style="color:var(${c})"` : ""}>${v}</div>` +
		`<div class="m">${m || ""}</div></div>`;

	const b = o.buying, dm = o.demand, w = o.when, g = o.ground;
	const cov = dm.coverage_pct || 0;
	const after = dm.after_risk_pct;

	const tiles = [
		tile(__("Buying"), int(b.units),
			`${esc(b.entry_stage || b.method || "")}` +
			(b.overridden ? ` · ${__("set by hand, sum said {0}", [int(b.calculated)])}` : "") +
			(b.supplier ? ` · ${esc(b.supplier)}` : ""),
			b.overridden ? "--warning" : ""),
		tile(__("Order by"), day(b.order_by),
			b.order_late ? __("that date has passed") : __("to arrive in time"),
			b.order_late ? "--critical" : "--good"),
	];
	if (b.propagates_here) {
		tiles.push(tile(__("Pool it builds"), int(b.pool),
			__("mother plants · {0} cuttings a week · {1} cycles",
				[int(b.weekly_draw), b.cycles])));
		tiles.push(tile(__("Cutting weeks"),
			b.cutting_weeks == null ? "—" : int(b.cutting_weeks),
			__("left in the line at {0} cycles", [b.cycles]),
			!b.cutting_weeks ? "--critical"
				: (b.cutting_weeks < 13 ? "--serious" : "--good")));
	}
	tiles.push(tile(__("Demand met"), cov.toFixed(1) + "%",
		__("{0} of {1} stems", [int(dm.planned), int(dm.asked)]),
		cov >= 100 ? "--good" : "--serious"));
	if (dm.at_risk) {
		tiles.push(tile(__("After cuttings short"),
			after == null ? "—" : after.toFixed(1) + "%",
			dm.risk_exceeds_plan
				? __("every stem this plan grows is short of cuttings")
				: __("{0} stems have no cuttings to grow them", [int(dm.at_risk)]),
			"--critical"));
	}
	tiles.push(tile(__("Going in the ground"), int(g.plants),
		__("{0} plantings · {1} beds of {2}", [int(g.plantings), int(g.beds),
			int(g.beds_available)]),
		g.capped ? "--serious" : ""));
	tiles.push(tile(__("First harvest"), w.first_harvest_week || "—",
		w.last_harvest_week && w.last_harvest_week !== w.first_harvest_week
			? __("through {0}", [w.last_harvest_week]) : ""));

	// Order, arrive, plant, cut -- the four dates a cohort has, on one row.
	const rows = (o.arrivals || []).map((a) =>
		`<tr${a.late ? ' style="color:var(--red-600,#c0392b)"' : ""}>` +
		`<td>${esc(a.planting_week || "—")}</td>` +
		`<td>${day(a.order_by)}</td>` +
		`<td>${day(a.arrives)}</td>` +
		`<td>${day(a.planting_date)}</td>` +
		`<td>${esc(a.first_harvest_week || "—")}</td>` +
		`<td class="text-right">${int(a.plants)}</td>` +
		`<td class="text-right">${int(a.beds)}</td>` +
		`<td class="text-right">${a.from_pool ? __("off the pool") : int(a.units)}</td>` +
		`</tr>`).join("");

	el.html(`<style>
.sfp-wrap{margin:0 0 6px}
.sfp-tiles{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:14px}
.sfp-tile{border:1px solid var(--border-color);border-radius:8px;padding:9px 11px;background:var(--card-bg)}
.sfp-tile .k{font-size:.66rem;text-transform:uppercase;letter-spacing:.5px;color:var(--text-muted)}
.sfp-tile .v{font-size:1.18rem;font-weight:600;line-height:1.35;font-variant-numeric:tabular-nums}
.sfp-tile .m{font-size:.72rem;color:var(--text-muted)}
.sfp-basis{font-size:.76rem;color:var(--text-muted);margin:0 0 12px}
table.sfp-t{width:100%;border-collapse:collapse;font-size:.78rem}
table.sfp-t th{text-align:left;font-size:.64rem;text-transform:uppercase;letter-spacing:.5px;
  color:var(--text-muted);padding:5px 8px;border-bottom:1px solid var(--border-color)}
table.sfp-t td{padding:4px 8px;border-bottom:1px solid var(--border-color);
  font-variant-numeric:tabular-nums}
table.sfp-t th.text-right,table.sfp-t td.text-right{text-align:right}
.sfp-scroll{max-height:340px;overflow:auto;border:1px solid var(--border-color);border-radius:8px}
</style>
<div class="sfp-wrap">
  <div class="sfp-tiles">${tiles.join("")}</div>
  ${b.basis ? `<p class="sfp-basis">${esc(b.basis)}</p>` : ""}
  ${dm.risk_note ? `<p class="sfp-basis">${esc(dm.risk_note)}</p>` : ""}
  <div class="sfp-scroll"><table class="sfp-t"><thead><tr>
    <th>${__("Planting week")}</th><th>${__("Order by")}</th><th>${__("Material arrives")}</th>
    <th>${__("Planted")}</th><th>${__("First harvest")}</th>
    <th class="text-right">${__("Plants")}</th><th class="text-right">${__("Beds")}</th>
    <th class="text-right">${__("To buy")}</th>
  </tr></thead><tbody>${rows}</tbody></table></div>
</div>`);
}
