/* Who propagates a plan's material and what they deliver to the farm.
 * Choose who; shares, lead time, rate and the deliveries to the farm follow.
 *
 *   sfPropagation.load(el, plan, call?, {editable, onSaved})
 */
(function () {
	if (window.sfPropagation) return;
	const M = "upande_summer_flowers.summer_flowers.propagation_edit.";
	const esc = (v) => String(v == null ? "" : v).replace(/[&<>"]/g, (c) =>
		({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
	const num = (v) => Math.round(+v || 0).toLocaleString();

	function render(el, s, o) {
		const edit = o.editable && s.docstatus === 0;
		const chosen = s.chosen || [];
		const taken = new Set(chosen.map((c) => c.party));
		const opts = (list, label, fmt) => list.filter((x) => !taken.has(x.name)).length
			? `<optgroup label="${label}">` + list.filter((x) => !taken.has(x.name))
				.map((x) => `<option value="${esc(x.name)}" data-kind="${x.kind}">${esc(x.name)} — ${fmt(x)}</option>`).join("")
				+ `</optgroup>` : "";
		const f = o._filter || (o._filter = { week: "", party: "" });
		let arr = s.arrivals || [];
		const weeks = [...new Set(arr.map((a) => a.week))].sort();
		if (f.week) arr = arr.filter((a) => a.week === f.week);
		if (f.party) arr = arr.filter((a) => a.party === f.party);
		const limit = f.week || f.party ? 0 : 20;

		let h = `<div class="sfo">`;
		h += `<h4>Who propagates <small>${num(s.plants)} plants to raise for this plan</small></h4>`;
		if (!chosen.length) h += `<div class="sfo-empty">Nobody chosen yet.</div>`;
		else {
			h += `<div class="sfo-scroll"><table><thead><tr><th>Propagator</th><th>Kind</th><th class="r">Share</th>`
				+ `<th class="r">Plants</th><th class="r">Starts before planting</th><th class="r">Rate / plant</th><th class="r">Cost</th>`
				+ (edit ? "<th></th>" : "") + `</tr></thead><tbody>`
				+ chosen.map((c) => `<tr data-party="${esc(c.party)}" data-kind="${c.kind}">`
					+ `<td>${esc(c.party)}</td><td class="dim">${c.kind === "outside" ? "Outside propagator" : "Our own unit"}</td>`
					+ `<td class="r">${edit ? `<input class="sfo-in num" data-share value="${c.share_pct}" style="width:5em"> %` : c.share_pct + "%"}</td>`
					+ `<td class="r">${num(c.plants)}</td><td class="r">${c.lead_weeks} weeks</td>`
					+ `<td class="r">${c.kind === "outside" ? (edit ? `<input class="sfo-in num" data-rate value="${c.rate}" style="width:6em">` : c.rate) : "—"}</td>`
					+ `<td class="r">${c.kind === "outside" ? num(c.amount) : "—"}</td>`
					+ (edit ? `<td><button class="sfo-btn sfo-x" data-remove title="Remove">✕</button></td>` : "") + `</tr>`).join("")
				+ `</tbody></table></div>`;
			if (s.cost) h += `<div class="sfo-note">Outside propagation costs ${num(s.cost)} in all.</div>`;
		}
		if (edit) {
			h += `<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:8px">`
				+ `<select class="sfo-in" data-add><option value="">Add who propagates…</option>`
				+ opts(s.units || [], "Our propagation units", (u) => `${num(u.free)} plants free${u.here ? " · on this farm" : ""}`)
				+ opts(s.outside || [], "Outside propagators", (p) => `${p.lead_weeks} weeks · ${p.rate} a plant`)
				+ `</select>`
				+ (chosen.length > 1 ? `<button class="sfo-btn" data-even>Share evenly</button>` : "")
				+ (chosen.length ? `<button class="sfo-btn pri" data-save>Save shares and rates</button>` : "")
				+ `</div>`;
		}

		h += `<h4>Deliveries to the farm <small>${(s.arrivals || []).length} — each planting week, every propagator hands over its share</small></h4>`;
		if (!(s.arrivals || []).length) {
			h += `<div class="sfo-empty">Choose who propagates and their deliveries to the farm follow the plantings.</div>`;
		} else {
			h += `<div style="display:flex;gap:8px;align-items:center;margin-bottom:6px"><b style="font-size:12px">Show</b>`
				+ `<select class="sfo-in" data-f="week"><option value="">Every week</option>${weeks.map((w) => `<option${w === f.week ? " selected" : ""}>${esc(w)}</option>`).join("")}</select>`
				+ (chosen.length > 1 ? `<select class="sfo-in" data-f="party"><option value="">Every propagator</option>${chosen.map((c) => `<option${c.party === f.party ? " selected" : ""}>${esc(c.party)}</option>`).join("")}</select>` : "")
				+ `</div>`;
			h += `<div class="sfo-scroll"><table><thead><tr><th>Planting week</th><th>Propagator</th><th class="r">Plants to the farm</th>`
				+ `<th class="r">Standing after planting</th><th>Start by</th></tr></thead><tbody>`
				+ arr.map((a, i) => `<tr${limit && i >= limit ? ' class="sfp-more" style="display:none"' : ""}>`
					+ `<td>${esc(a.week)}</td><td>${esc(a.party)}</td><td class="r">${num(a.plants)}</td>`
					+ `<td class="r">${num(a.standing)}</td><td>${esc(a.start)}</td></tr>`).join("")
				+ `</tbody></table></div>`
				+ (limit && arr.length > limit ? `<a class="sfo-more" data-showall>Show all ${arr.length}</a>` : "");
		}
		h += `</div>`;
		el.innerHTML = h;
		wire(el, s, o);
	}

	function wire(el, s, o) {
		const save = (picks) => {
			el.querySelectorAll(".sfo-in,.sfo-btn").forEach((x) => (x.disabled = true));
			return o.call(M + "set_propagators", { plan: s.plan, picks: JSON.stringify(picks) })
				.then((n) => { render(el, n, o); if (o.onSaved) o.onSaved(n); },
					(e) => { render(el, s, o); const m = (e && e.message) || String(e);
						if (window.frappe && frappe.msgprint) frappe.msgprint(m); else alert(m); });
		};
		const current = (even) => [...el.querySelectorAll("tr[data-party]")].map((tr) => ({
			party: tr.dataset.party, kind: tr.dataset.kind,
			share_pct: even ? "" : ((tr.querySelector("[data-share]") || {}).value ?? ""),
			rate: (tr.querySelector("[data-rate]") || {}).value,
		}));
		const add = el.querySelector("[data-add]");
		if (add) add.addEventListener("change", () => {
			if (!add.value) return;
			const kind = add.selectedOptions[0].dataset.kind;
			// A new propagator: everyone shares evenly until shares are typed.
			save(current(true).concat([{ party: add.value, kind, share_pct: "" }]));
		});
		el.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", () => {
			const gone = b.closest("tr").dataset.party;
			save(current(true).filter((p) => p.party !== gone));
		}));
		const even = el.querySelector("[data-even]");
		if (even) even.addEventListener("click", () => save(current(true)));
		const sv = el.querySelector("[data-save]");
		if (sv) sv.addEventListener("click", () => save(current(false)));
		el.querySelectorAll("[data-f]").forEach((x) => x.addEventListener("change", () => {
			o._filter[x.dataset.f] = x.value; render(el, s, o);
		}));
		const all = el.querySelector("[data-showall]");
		if (all) all.addEventListener("click", () => {
			el.querySelectorAll("tr.sfp-more").forEach((tr) => (tr.style.display = "")); all.remove();
		});
	}

	function load(el, plan, call, opts) {
		if (!el || !plan) return Promise.resolve();
		if (window.sfPlanOverview && window.sfPlanOverview.style) window.sfPlanOverview.style();
		const o = Object.assign({}, opts || {});
		o.call = call || ((m, a) => frappe.call({ method: m, args: a }).then((r) => r && r.message));
		el.innerHTML = `<div class="sfo"><div class="sfo-empty">Loading…</div></div>`;
		return o.call(M + "propagation", { plan }).then((s) => render(el, s, o),
			(e) => { el.innerHTML = `<div class="sfo"><div class="sfo-empty">Could not load: ${esc((e && e.message) || e)}</div></div>`; });
	}

	window.sfPropagation = { load, render };
})();
