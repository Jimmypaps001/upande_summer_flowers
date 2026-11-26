/* A production plan at a glance: how the material is bought, raised, planted
 * and cut. Drawn from plan_overview.plan_overview, the same payload on the
 * plan form and on the planning dashboard.
 *
 *   sfPlanOverview.load(el, plan)   fetch and draw
 *   sfPlanOverview.render(el, data) draw a payload already fetched
 */
(function () {
	if (window.sfPlanOverview) return;
	const METHOD = "upande_summer_flowers.summer_flowers.plan_overview.plan_overview";

	const CSS = `
.sfo{--t:var(--text-color,#1f272e);--t2:var(--text-muted,#6b7280);--bd:var(--border-color,rgba(0,0,0,.12));
 --bg2:var(--subtle-fg,rgba(0,0,0,.035));--ok:#0ca30c;--warn:#c98a00;--bad:#d03b3b;--s1:#2a78d6;--s2:#d95926;
 color:var(--t);font-size:13px;line-height:1.45}
.sfo h4{font-size:14px;font-weight:600;margin:22px 0 8px}
.sfo h4 small{font-weight:400;color:var(--t2);margin-left:6px}
.sfo .sfo-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px}
.sfo .sfo-k{border:1px solid var(--bd);border-radius:8px;padding:8px 10px}
.sfo .sfo-k b{display:block;font-size:16px;font-variant-numeric:tabular-nums}
.sfo .sfo-k span{font-size:11px;color:var(--t2)}
.sfo .sfo-k.bad b{color:var(--bad)} .sfo .sfo-k.ok b{color:var(--ok)}
.sfo .sfo-scroll{overflow-x:auto;border:1px solid var(--bd);border-radius:8px}
.sfo table{width:100%;border-collapse:collapse;font-size:12.5px}
.sfo th{text-align:left;font-weight:600;font-size:11.5px;color:var(--t2);background:var(--bg2);padding:6px 10px;white-space:nowrap}
.sfo td{padding:6px 10px;border-top:1px solid var(--bd);vertical-align:top}
.sfo .r{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.sfo .dim{color:var(--t2)}
.sfo .pill{display:inline-block;font-size:11px;padding:0 7px;border-radius:9px;margin-left:4px;white-space:nowrap}
.sfo .pill.bad{background:rgba(208,59,59,.12);color:var(--bad)}
.sfo .pill.warn{background:rgba(201,138,0,.14);color:var(--warn)}
.sfo .pill.ok{background:rgba(12,163,12,.12);color:var(--ok)}
.sfo .sfo-chain{display:flex;flex-wrap:wrap;align-items:stretch;gap:6px}
.sfo .sfo-step{border:1px solid var(--bd);border-radius:8px;padding:6px 10px;min-width:110px}
.sfo .sfo-step b{display:block}
.sfo .sfo-arrow{align-self:center;color:var(--t2)}
.sfo .sfo-more{display:block;margin-top:6px;font-size:12px;cursor:pointer;color:var(--s1)}
.sfo .sfo-chart{border:1px solid var(--bd);border-radius:8px;padding:8px}
.sfo .sfo-legend{font-size:11.5px;color:var(--t2);margin-bottom:4px}
.sfo .sfo-legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 10px;vertical-align:-1px}
.sfo .sfo-note{font-size:12px;color:var(--t2);margin-top:6px}
.sfo .sfo-empty{padding:18px;color:var(--t2)}
.sfo input.sfo-in,.sfo select.sfo-in{height:28px;padding:0 6px;border:1px solid var(--bd);border-radius:6px;font:inherit;font-size:12.5px;background:transparent;color:inherit}
.sfo input.sfo-in.num{width:7.5em;text-align:right}.sfo input.sfo-in.wk{width:7.5em}
.sfo .sfo-btn{height:28px;padding:0 10px;border:1px solid var(--bd);border-radius:6px;background:transparent;color:inherit;font:inherit;font-size:12px;cursor:pointer}
.sfo .sfo-btn.pri{background:var(--s1);border-color:var(--s1);color:#fff}
.sfo .sfo-btn:disabled{opacity:.5;cursor:default}
.sfo .sfo-x{color:var(--bad);border-color:transparent}
.sfo .sfo-change{border:1px solid var(--bd);border-left:4px solid var(--s1);border-radius:8px;padding:10px 12px;margin:10px 0}
.sfo .sfo-change b{font-weight:600}
.sfo .sfo-change .up{color:var(--ok)} .sfo .sfo-change .down{color:var(--bad)}
.sfo tr.sfo-busy td{opacity:.5}
`;

	function style() {
		if (document.getElementById("sfo-style")) return;
		const s = document.createElement("style");
		s.id = "sfo-style";
		s.textContent = CSS;
		document.head.appendChild(s);
	}
	const esc = (v) => String(v == null ? "" : v).replace(/[&<>"]/g, (c) =>
		({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
	const num = (v) => (v == null || v === "" ? "—" : Math.round(+v || 0).toLocaleString());
	const pct = (v) => (v == null ? "—" : (+v).toFixed(v >= 10 ? 0 : 1) + "%");
	const table = (head, rows, opts) => {
		opts = opts || {};
		const limit = opts.limit || 0;
		const id = "sfo" + Math.random().toString(36).slice(2, 8);
		const body = rows.map((r, i) =>
			`<tr${limit && i >= limit ? ` class="${id}" style="display:none"` : ""}>${r}</tr>`).join("");
		return `<div class="sfo-scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`
			+ (limit && rows.length > limit
				? `<a class="sfo-more" data-more="${id}">Show all ${rows.length}</a>` : "");
	};

	function milestones(d) {
		return `<div class="sfo-row">${(d.milestones || []).map((m) =>
			`<div class="sfo-k"><span>${esc(m.label)}</span><b>${esc(m.week || "—")}</b></div>`).join("")}</div>`;
	}

	function meets(d) {
		const f = d.figures || {};
		const k = (label, val, cls) => `<div class="sfo-k ${cls || ""}"><span>${esc(label)}</span><b>${val}</b></div>`;
		return `<h4>Against the demand</h4><div class="sfo-row">`
			+ k("Demand (stems)", num(f.demand))
			+ k("This plan cuts", num(f.production))
			+ k("Cover", pct(f.coverage_pct), f.coverage_pct >= 90 ? "ok" : "bad")
			+ k("Weeks short", num(f.weeks_short) + (f.stems_short ? ` <span class="dim" style="font-size:11px">${num(f.stems_short)} stems</span>` : ""), f.weeks_short ? "bad" : "ok")
			+ k("Stems over demand", num(f.stems_over))
			+ (d.bought ? k("Cut on time", pct(f.on_time_pct), f.on_time_pct >= 90 ? "ok" : "bad") : "")
			+ k("New plants", num(f.plants))
			+ k("Area to plant (m²)", num(f.area_sqm))
			+ (f.not_placed ? k("Plantings with no block", num(f.not_placed), "bad") : "")
			+ `</div>`;
	}

	function buying(d) {
		if (!d.bought) {
			return `<h4>How it is bought</h4><div class="sfo-empty">Nothing is bought: `
				+ `${esc((d.route || []).map((r) => r.stage).join(" → ") || "the material")} is raised from the farm's own crop.</div>`;
		}
		const sup = d.suppliers || [];
		let h = "";
		if (canEdit(d, true) && d.supply_source !== "saved" && (d.deliveries || []).length) {
			h += `<div class="sfo-change">These deliveries are a proposal worked out from the demand. `
				+ `<button class="sfo-btn pri" data-act="adopt">Use them as this plan's deliveries</button> `
				+ `to change suppliers, weeks or quantities.</div>`;
		}
		h += `<h4>How it is bought <small>${esc(d.supply_mode)}${d.supply_agreed ? " · agreed" : d.supply_source === "saved" ? " · saved, not yet agreed" : " · proposed"}</small></h4>`;
		h += table("<th>Supplier</th><th class='r'>Share</th><th class='r'>Delivers every</th><th class='r'>Lead time</th>",
			sup.map((s) => `<td>${esc(s.supplier)}${s.named ? "" : ' <span class="pill warn">not named yet</span>'}</td>`
				+ `<td class="r">${pct(s.share_pct)}</td><td class="r">${s.cycle_weeks ? s.cycle_weeks + " weeks" : "once"}</td>`
				+ `<td class="r">${s.lead_weeks} weeks</td>`));
		const unit = esc(d.buy_stage || "units");
		const halves = d.halves || [];
		if (halves.length) {
			h += `<div style="height:10px"></div>`;
			h += table(`<th>Period</th><th>Weeks</th><th class="r">${unit} to buy</th><th class="r">Deliveries</th>`
				+ `<th class="r">Per delivery</th>${sup.map((s) => `<th class="r">${esc(s.supplier)}, each delivery</th>`).join("")}`,
				halves.map((x) => `<td>${esc(x.label)}</td>`
					+ `<td class="dim">${esc(x.from)} – ${esc(x.to)}</td><td class="r">${canEdit(d)
						? `<input class="sfo-in num" data-half-units data-from="${esc(x.from)}" data-to="${esc(x.to)}" value="${x.units}">`
							+ ` <button class="sfo-btn" data-act="half" data-from="${esc(x.from)}" data-to="${esc(x.to)}">Set</button>`
						: `<b>${num(x.units)}</b>`}</td>`
					+ `<td class="r">${x.deliveries}</td><td class="r">${num(x.per_delivery_units)}</td>`
					+ sup.map((s, i) => {
						const p = (x.per_supplier || [])[i] || {};
						return `<td class="r">${p.deliveries ? num(p.per_delivery_units) + ` <span class="dim">× ${p.deliveries}</span>` : "—"}</td>`;
					}).join("")));
			const t = d.totals || {};
			h += `<div class="sfo-note">${num(t.units_to_buy)} ${unit} in ${t.deliveries} deliveries, `
				+ `for ${num(t.plants_to_plant)} plants to plant (${num(d.plants_per_unit * 100)} plants for every 100 bought).`
				+ (d.second_half_revisable_until ? ` The next period's figure can be changed until ${esc(d.second_half_revisable_until)}, its order week.` : "")
				+ `</div>`;
		}
		return h;
	}

	function deliveries(d) {
		const rows = d.deliveries || [];
		if (!rows.length) return "";
		const lifted = rows.some((r) => (r.lifts || []).length > 1);
		const unit = esc(d.buy_stage || "Units");
		const past = (d.totals || {}).past_deliveries;
		const edit = canEdit(d) && d.supply_source === "saved";
		const names = (d._suppliers || (d.suppliers || []).map((s) => s.supplier));
		const pick = (cur) => `<select class="sfo-in" data-f="supplier">${[cur].concat(names.filter((n) => n !== cur))
			.map((n) => `<option${n === cur ? " selected" : ""}>${esc(n)}</option>`).join("")}</select>`;
		let h = `<h4>Deliveries <small>${rows.length}${past ? ` · <span class="pill bad">${past} should already have been ordered</span>` : ""}</small></h4>`
			+ table(`<th>#</th><th>Order by</th><th>Arrives</th><th>Supplier</th><th class="r">${unit}</th>`
				+ (lifted ? "<th>Planted (lifts)</th>" : "<th>Planted</th>") + (edit ? "<th></th>" : ""),
				rows.map((r) => {
					const who = (r.suppliers || []).map((s) => s.supplier).join(", ");
					return `<td>${r.no}</td><td>${esc(r.order_by)}${r.past ? ' <span class="pill bad">past</span>' : ""}</td>`
					+ (edit
						? `<td><input class="sfo-in wk" data-f="arrives" value="${esc(r.arrives)}" data-row="${esc(r.row)}"></td>`
							+ `<td data-row="${esc(r.row)}">${pick(who)}</td>`
							+ `<td class="r"><input class="sfo-in num" data-f="units" value="${r.units}" data-row="${esc(r.row)}"></td>`
						: `<td>${esc(r.arrives)}</td><td>${esc(who)}</td><td class="r">${num(r.units)}</td>`)
					+ `<td>${(r.lifts || []).length
						? r.lifts.map((L) => `${esc(L.week)} <span class="dim">${L.share_pct}% · ${num(L.plants)}</span>`).join("<br>")
						: esc(r.first_planting)}</td>`
					+ (edit ? `<td><button class="sfo-btn sfo-x" data-act="remove" data-row="${esc(r.row)}" title="Remove this delivery">✕</button></td>` : "");
				}), { limit: edit ? 0 : 12 });
		if (edit) {
			h += `<div class="sfo-row" style="grid-template-columns:auto auto auto auto;justify-content:start;align-items:center;margin-top:8px;gap:6px">`
				+ `<span class="dim">Add a delivery:</span>${pick(names[0] || "")}`
				+ `<input class="sfo-in wk" data-new="arrives" placeholder="2028-W30">`
				+ `<input class="sfo-in num" data-new="units" placeholder="${unit}">`
				+ `<button class="sfo-btn pri" data-act="add">Add</button></div>`
				+ `<div class="sfo-note">Change a supplier, an arrival week or a quantity and the plan is redrawn from the deliveries: `
				+ `their lifts are planted and the harvest below is worked out again.</div>`;
		}
		return h;
	}

	function raised(d) {
		const ways = d.ways || [];
		if (!ways.length) return "";
		let h = `<h4>How the material is raised</h4>`;
		ways.forEach((w) => {
			if (ways.length > 1) h += `<div class="dim" style="margin:8px 0 4px">${pct(w.share_pct)} of the material</div>`;
			h += `<div class="sfo-chain">` + w.stages.map((s, i) =>
				(i ? `<span class="sfo-arrow">→</span>` : "")
				+ `<div class="sfo-step"><b>${esc(s.stage)}</b><span class="dim">`
				+ [s.bought ? "bought" : "", s.weeks ? s.weeks + " weeks" : "", s.loss_pct ? s.loss_pct + "% lost" : "",
					s.standing ? "stays and is cut from" : ""].filter(Boolean).join(" · ")
				+ `</span></div>`).join("") + `</div>`;
			h += `<div class="sfo-note">Of every 100 ${esc(w.buy_stage || "units")} bought, ${num(w.survival * 100)} become standing plants.`
				+ (w.lifts && w.lifts.length
					? ` Each delivery is planted in ${w.lifts.length} lifts: `
						+ w.lifts.map((L) => `${L.share_pct}% at ${L.weeks_after} weeks`).join(", ")
						+ " after it arrives, and every lift is planted."
					: "") + `</div>`;
		});
		return h;
	}

	function example(d) {
		const e = d.example;
		if (!e) {
			return d.pooled ? `<h4>From delivery to stems</h4><div class="sfo-empty">The ${esc(d.buy_stage || "TC")} builds `
				+ `the mother stock; plantings are cut from it, so a delivery is not planted directly.</div>` : "";
		}
		const st = e.stages || [];
		let h = `<h4>One delivery, start to finish <small>delivery ${e.delivery}: ${num(e.units)} ${esc(d.buy_stage || "units")}, arrives ${esc(e.arrives)}</small></h4>`;
		h += table(`<th>Lift</th><th>Planted</th><th class="r">${esc(d.buy_stage || "Units")}</th>`
			+ st.map((s) => `<th class="r">After ${esc(s)}</th>`).join(""),
			e.lifts.map((L) => `<td>${L.lift_no} <span class="dim">(${L.share_pct}%)</span></td><td>${esc(L.planted)}</td>`
				+ `<td class="r">${num(L.units)}</td>` + (L.after || []).map((a) => `<td class="r">${num(a.plants)}</td>`).join(""))
			.concat([`<td><b>Total</b></td><td></td><td class="r"><b>${num(e.units)}</b></td>`
				+ st.map((_s, i) => `<td class="r"><b>${num(e.lifts.reduce((t, L) => t + ((((L.after || [])[i]) || {}).plants || 0), 0))}</b></td>`).join("")]));
		if ((e.cuts || []).length) {
			h += `<div style="height:10px"></div>`;
			h += table(`<th>Harvest week</th>${e.lifts.map((L) => `<th class="r">From lift ${L.lift_no}</th>`).join("")}<th class="r">Stems</th>`,
				e.cuts.map((c) => `<td>${esc(c.week)}</td>${c.by_lift.map((x) => `<td class="r">${x ? num(x) : ""}</td>`).join("")}`
					+ `<td class="r"><b>${num(c.stems)}</b></td>`));
			h += `<div class="sfo-note">The first ${e.cuts.length} harvest weeks. Over its whole life this delivery cuts about ${num(e.life_stems)} stems `
				+ `(${num(e.standing)} plants × ${d.stems_per_plant_life} stems each).</div>`;
		}
		return h;
	}

	function planting(d) {
		const rows = d.plantings || [];
		if (!rows.length) return `<h4>Planting</h4><div class="sfo-empty">No plantings yet.</div>`;
		return `<h4>Planting <small>${rows.length} weeks · ${num(rows.reduce((t, r) => t + r.plants_to_plant, 0))} plants</small></h4>`
			+ table(`<th>Week</th><th class="r">Plants to plant</th><th class="r">Area (m²)</th><th>Blocks</th><th>First harvest</th>`,
				rows.map((r) => `<td>${esc(r.week)}${r.too_late ? ' <span class="pill bad">too late to order</span>' : ""}`
					+ `${r.not_placed ? ' <span class="pill warn">no block</span>' : ""}</td>`
					+ `<td class="r">${num(r.plants_to_plant)}</td><td class="r">${num(r.area_sqm)}</td>`
					+ `<td class="dim">${esc((r.blocks || []).join(", ") || "—")}</td><td>${esc(r.first_harvest || "—")}</td>`), { limit: 12 });
	}

	function chart(weeks) {
		if (!weeks.length) return "";
		const W = 900, H = 180, pad = 28;
		const max = Math.max(1, ...weeks.map((w) => Math.max(w.demand, w.production)));
		const bw = (W - pad) / weeks.length;
		const y = (v) => H - 18 - (v / max) * (H - 30);
		const bars = weeks.map((w, i) => `<rect x="${pad + i * bw + 1}" y="${y(w.production)}" width="${Math.max(1, bw - 2)}"`
			+ ` height="${H - 18 - y(w.production)}" fill="var(--s1)" opacity=".75"><title>${esc(w.week)}: ${num(w.production)} cut, ${num(w.demand)} wanted</title></rect>`).join("");
		const line = weeks.map((w, i) => `${pad + i * bw + bw / 2},${y(w.demand)}`).join(" ");
		const ticks = weeks.map((w, i) => (i % Math.ceil(weeks.length / 8) === 0
			? `<text x="${pad + i * bw}" y="${H - 4}" font-size="10" fill="currentColor" opacity=".6">${esc(w.week)}</text>` : "")).join("");
		return `<div class="sfo-chart"><div class="sfo-legend"><i style="background:var(--s1)"></i>Stems cut`
			+ `<i style="background:var(--s2)"></i>Demand</div>`
			+ `<svg viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="none" style="display:block;height:${H}px">`
			+ `<text x="0" y="12" font-size="10" fill="currentColor" opacity=".6">${num(max)}</text>${bars}`
			+ `<polyline points="${line}" fill="none" stroke="var(--s2)" stroke-width="2"/>${ticks}</svg></div>`;
	}

	function harvest(d) {
		const months = d.months || [];
		let h = `<h4>Harvest against demand</h4>` + chart(d.weeks || []);
		if (months.length) {
			h += `<div style="height:10px"></div>` + table(`<th>Month</th><th class="r">Demand</th><th class="r">Stems cut</th><th class="r">Difference</th>`,
				months.map((m) => {
					const v = m.production - m.demand;
					return `<td>${esc(m.label)}</td><td class="r">${num(m.demand)}</td><td class="r">${num(m.production)}</td>`
						+ `<td class="r" style="color:${v < 0 ? "var(--bad)" : "inherit"}">${v > 0 ? "+" : ""}${num(v)}</td>`;
				}));
		}
		return h;
	}

	// Draft plans whose deliveries can be changed from the page.
	function canEdit(d, anySource) {
		return !!(d && d._editable && d.docstatus === 0 && d.bought && !d.pooled
			&& (anySource || d.supply_source === "saved"));
	}

	function changeBanner(r) {
		if (!r || !r.before || !r.after) return "";
		const b = r.before, a = r.after;
		const line = (label, x, y, goodUp, fmtf) => {
			fmtf = fmtf || num;
			if (x === y) return `${label} ${fmtf(y)}`;
			const better = goodUp ? y > x : y < x;
			return `${label} ${fmtf(x)} → <b class="${better ? "up" : "down"}">${fmtf(y)}</b>`;
		};
		return `<div class="sfo-change"><b>${esc(r.done || "Changed")}</b><br>`
			+ [line("Cover", b.coverage_pct, a.coverage_pct, true, pct),
				line("Weeks short", b.weeks_short, a.weeks_short, false),
				line("Stems short", b.stems_short, a.stems_short, false),
				line("Stems over demand", b.stems_over, a.stems_over, false),
				line("TC bought", b.units, a.units, false),
				line("Deliveries", b.deliveries, a.deliveries, false)].join(" &nbsp;·&nbsp; ")
			+ `</div>`;
	}

	// The filter the page is showing: one week and/or one supplier, or the
	// whole season. Kept on the payload so a redraw after an edit keeps it.
	function filt(d) { return d._filter || (d._filter = { week: "", supplier: "" }); }

	function weekOptions(d) {
		const set = new Set();
		(d.deliveries || []).forEach((r) => {
			if (r.order_by) set.add(r.order_by);
			if (r.arrives) set.add(r.arrives);
			(r.lifts || []).forEach((L) => L.week && set.add(L.week));
		});
		(d.plantings || []).forEach((p) => set.add(p.week));
		(d.weeks || []).forEach((w) => set.add(w.week));
		return [...set].sort();
	}

	function filters(d) {
		const f = filt(d);
		const sups = [...new Set((d.deliveries || []).flatMap((r) => (r.suppliers || []).map((s) => s.supplier)))];
		return `<div class="sfo-filters" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:14px 0 4px">`
			+ `<b style="font-size:12px">Show</b>`
			+ `<select class="sfo-in" data-filter="week"><option value="">The whole season</option>`
			+ weekOptions(d).map((w) => `<option${w === f.week ? " selected" : ""}>${esc(w)}</option>`).join("")
			+ `</select>`
			+ (sups.length > 1 ? `<select class="sfo-in" data-filter="supplier"><option value="">Every supplier</option>`
				+ sups.map((n) => `<option${n === f.supplier ? " selected" : ""}>${esc(n)}</option>`).join("") + `</select>` : "")
			+ (f.week || f.supplier ? `<button class="sfo-btn" data-filter-clear>Clear</button>` : "")
			+ `</div>`;
	}

	// Every week of the plan in one table: ordered, arriving, planted, wanted, cut.
	function seasonTable(d) {
		const f = filt(d);
		const row = {};
		const at = (w) => (row[w] = row[w] || { week: w, ordered: 0, arrives: 0, planted: 0, sup: [] , demand: null, cut: null });
		(d.deliveries || []).forEach((r) => {
			const who = (r.suppliers || []).map((s) => s.supplier);
			if (f.supplier && !who.includes(f.supplier)) return;
			if (r.order_by) { at(r.order_by).ordered += r.units; at(r.order_by).sup.push(...who); }
			if (r.arrives) at(r.arrives).arrives += r.units;
			(r.lifts || []).forEach((L) => { if (L.week) at(L.week).planted += L.plants || 0; });
		});
		(d.weeks || []).forEach((w) => { const x = at(w.week); x.demand = w.demand; x.cut = w.production; });
		let rows = Object.values(row).sort((a, b) => (a.week < b.week ? -1 : 1));
		if (f.week) rows = rows.filter((x) => x.week === f.week);
		if (!rows.length) return "";
		const unit = esc(d.buy_stage || "TC");
		return `<h4>Week by week <small>${f.week ? esc(f.week) : "the whole season, from the first order to the last harvest"}</small></h4>`
			+ table(`<th>Week</th><th class="r">${unit} ordered</th><th class="r">${unit} arriving</th><th class="r">Plants planted</th>`
				+ `<th class="r">Demand</th><th class="r">Cut</th><th class="r">Difference</th>`,
				rows.map((x) => {
					const diff = x.demand == null ? null : (x.cut || 0) - (x.demand || 0);
					return `<td>${esc(x.week)}</td><td class="r">${x.ordered ? num(x.ordered) : ""}</td>`
						+ `<td class="r">${x.arrives ? num(x.arrives) : ""}</td><td class="r">${x.planted ? num(x.planted) : ""}</td>`
						+ `<td class="r">${x.demand == null ? "" : num(x.demand)}</td><td class="r">${x.cut == null ? "" : num(x.cut)}</td>`
						+ `<td class="r" style="color:${diff != null && diff < 0 ? "var(--bad)" : "inherit"}">${diff == null ? "" : (diff > 0 ? "+" : "") + num(diff)}</td>`;
				}), { limit: f.week ? 0 : 20 });
	}

	// The payload as the filter leaves it: deliveries and plantings of that week / supplier.
	function filtered(d) {
		const f = filt(d);
		if (!f.week && !f.supplier) return d;
		const touches = (r) => !f.week || r.order_by === f.week || r.arrives === f.week
			|| (r.lifts || []).some((L) => L.week === f.week);
		const bySup = (r) => !f.supplier || (r.suppliers || []).some((s) => s.supplier === f.supplier);
		return Object.assign({}, d, {
			deliveries: (d.deliveries || []).filter((r) => touches(r) && bySup(r)),
			plantings: (d.plantings || []).filter((p) => !f.week || p.week === f.week),
		});
	}

	function render(el, d) {
		style();
		if (!el) return;
		if (!d || d.empty) {
			el.innerHTML = `<div class="sfo"><div class="sfo-empty">${esc((d && d.empty) || "Nothing to show yet.")}</div></div>`;
			return;
		}
		const v = filtered(d);
		el.innerHTML = `<div class="sfo">${changeBanner(d._last)}${milestones(d)}${meets(d)}${filters(d)}${seasonTable(d)}`
			+ `${buying(d)}${deliveries(v)}${planting(v)}${harvest(d)}${raised(d)}${example(d)}</div>`;
		el.querySelectorAll("[data-filter]").forEach((s) => s.addEventListener("change", () => {
			filt(d)[s.dataset.filter] = s.value; render(el, d);
		}));
		const clr = el.querySelector("[data-filter-clear]");
		if (clr) clr.addEventListener("click", () => { d._filter = null; render(el, d); });
		if (d._editable) wire(el, d);
		el.querySelectorAll("[data-more]").forEach((a) => a.addEventListener("click", () => {
			el.querySelectorAll("tr." + a.dataset.more).forEach((tr) => (tr.style.display = ""));
			a.remove();
		}));
	}

	const ED = "upande_summer_flowers.summer_flowers.delivery_edit.";

	function wire(el, d) {
		const go = d._call;
		const run = (method, args, btn) => {
			if (btn) btn.disabled = true;
			el.querySelectorAll(".sfo-in,.sfo-btn").forEach((x) => (x.disabled = true));
			return go(ED + method, Object.assign({ plan: d.plan }, args)).then((r) => {
				const next = Object.assign(r.overview || {}, { _editable: true, _call: go,
					_suppliers: d._suppliers, _onChanged: d._onChanged, _last: r.before ? r : null,
					_filter: d._filter });
				render(el, next);
				if (d._onChanged) d._onChanged(r);
			}, (e) => {
				el.querySelectorAll(".sfo-in,.sfo-btn").forEach((x) => (x.disabled = false));
				const msg = (e && e.message) || String(e || "Could not change it");
				if (window.frappe && frappe.msgprint) frappe.msgprint(msg); else alert(msg);
			});
		};
		el.querySelectorAll("[data-row][data-f]").forEach((inp) => {
			inp.addEventListener("change", () => {
				const args = { row: inp.dataset.row };
				args[inp.dataset.f] = inp.value.trim();
				run("set_delivery", args);
			});
		});
		el.querySelectorAll("td[data-row] select[data-f=supplier]").forEach((sel) => {
			sel.addEventListener("change", () => run("set_delivery", {
				row: sel.closest("td").dataset.row, supplier: sel.value }));
		});
		el.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => {
			const act = b.dataset.act;
			if (act === "adopt") return run("start_from_proposal", {}, b);
			if (act === "remove") {
				const ok = window.confirm ? window.confirm("Remove this delivery from the plan?") : true;
				return ok && run("remove_delivery", { row: b.dataset.row }, b);
			}
			if (act === "half") {
				const inp = el.querySelector(`[data-half-units][data-from="${b.dataset.from}"]`);
				return run("revise_half", { first_week: b.dataset.from, last_week: b.dataset.to,
					units: inp ? inp.value : "" }, b);
			}
			if (act === "add") {
				const box = b.parentElement;
				return run("add_delivery", {
					supplier: (box.querySelector("select") || {}).value || "",
					arrives: (box.querySelector("[data-new=arrives]") || {}).value || "",
					units: (box.querySelector("[data-new=units]") || {}).value || "" }, b);
			}
		}));
	}

	function load(el, plan, call, opts) {
		style();
		if (!el || !plan) return Promise.resolve();
		el.innerHTML = `<div class="sfo"><div class="sfo-empty">Loading…</div></div>`;
		const go = call || ((m, a) => frappe.call({ method: m, args: a }).then((r) => r && r.message));
		opts = opts || {};
		const get = opts.editable
			? Promise.all([go(METHOD, { plan }), go(ED + "suppliers", { plan }).catch(() => [])])
			: go(METHOD, { plan }).then((d) => [d, null]);
		return get.then(([d, names]) => {
			if (d && opts.editable) Object.assign(d, { _editable: true, _call: go,
				_suppliers: names || null, _onChanged: opts.onChanged || null });
			if (d && opts.last) d._last = opts.last;
			render(el, d);
		}, (e) => {
			el.innerHTML = `<div class="sfo"><div class="sfo-empty">Could not load the overview: ${esc((e && e.message) || e || "")}</div></div>`;
		});
	}

	window.sfPlanOverview = { render, load, METHOD, style };
})();
