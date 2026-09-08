# Changelog

Summer Flowers ships from `version-16` on a rolling basis: there are no tags, and a
commit is the unit of release. Each section below is a working milestone, newest
first, headed by the date its commits landed. Hashes are given so a site can be
matched to what it is actually running.

To find out what a site has, compare its behaviour rather than its version string —
`__version__` has been `0.0.1` throughout. The whitelisted methods named in each
section are a reliable probe: if a method exists, the commit that added it is
deployed.

---

## One protocol doctype again, and demand per farm — `6fb2cbc`, `94d1d9e` (2026-09-09)

`c2b8237` split the summer flower protocol onto its own doctype so agriculture's
Crop Protocol could move without breaking planning. It did its job, and it is now
reversed: the split left **two masters for the same varieties**, with 75 protocols on
both doctypes and the twelve newest Aster rows on only one, so `/desk/crop-protocol`
listed summer flowers without listing all of them.

Undoing it moved no data. Agriculture dropped `variety` and `farm` from the doctype
but not from the table, so both columns and their contents survived — `variety` on all
224 rows, `farm` on the 75 summer ones, agreeing with the record name on every one.
Re-declaring them as Custom Fields from this app re-exposes what is already there,
which keeps the ownership the split was after: agriculture's JSON still never mentions
a summer flower field.

**Probe:** `crop_protocol.PROTOCOL_DOCTYPE` is `"Crop Protocol"`, and
`frappe.get_meta("Crop Protocol")` has `variety` and `farm`.

**A naming bug went with it.** The autoname rule this app has always shipped,
`format:{variety}-{farm}`, had been resolving to the literal string `variety-farm`
ever since those two fields left the doctype. The first protocol anyone created took
that as its name and the next would have failed as a duplicate.

**One switch, not three.** `crop_type` is a two-option Select — Roses or Summer
Flowers — chosen straight after the farm. `custom_is_summer_flower` and
`custom_sf_crop_class` are derived from it in `before_validate` and hidden; every
Python guard still reads the flag and every field shows or hides on an `eval` of
`crop_type`. Narrowing `crop_type` lost nothing: it duplicated the variety Item's own
`item_group` on 236 of 237 records.

15 of the 101 rewritten `depends_on` rules could never have fired — they compared
`crop_type` against `"Rose"`, `"Spray Rose"` and `"Summer Flower"`, which no record
has ever held. That is why the rose cut-cycle and yield sections never appeared.

**Two guards had to move, because these hooks now fire for every crop.** `validate`
returned nothing early, so a rose save ran the whole summer flower derivation and
threw on the first thing it wanted — Crop Protocol Version refuses to derive without
a net m² per bed. And the `sf_protocol_move` guard lived on the `SummerFlowerProtocol`
class, which this retires.

**The form asked "Variety" twice**, on Crop Protocol and again on Crop Cycle. On Crop
Protocol `breeder`, `crop_type` and `variety_item` each existed as both a native
DocField and a stale Custom Field, so `get_meta` returned them twice.

**And selecting Roses showed nothing at all.** This app's whole block was anchored at
native field 7 of 31, so its four tabs opened in the middle of agriculture's field
order and every native field after that point fell inside one of them — 15 rose
fields sat in `custom_sf_tab_cycle` and `custom_sf_tab_grades`, correctly gated to
Roses and permanently unreachable, because the tab closed before the rule was
consulted. The block now follows the last native field: Roses reaches 20 fields
across 6 sections.

**`94d1d9e` — market demand per variety and farm.** Summer Flower Market Demand
carried a `farm` field but named itself `format:{variety}`, so a variety could hold
demand for one farm only. That is also what orphaned the grade rows: all seven still
carried older `<variety>-<farm>` parents. Naming now matches the protocol it is
planned against and `farm` is mandatory, so renaming the four existing records
re-adopts those rows. `Summer Flower Demand Grade` had rows but **no doctype pointed
a Table field at it**, leaving the split unreachable from any form; it is wired back
on as `grade_allocation`. `vbn_code` and `product_group` are added for the sheet's
buyer-side codes.

Grade stays a percentage rather than a column on the weekly row: the sheet's per-grade
figures are strict proportions of the variety's weekly total in all 52 weeks, and they
are the same percentages the orphaned rows already held.

**Probe:** `frappe.get_meta("Summer Flower Market Demand").autoname` is
`format:{variety}-{farm}`.

**Loading these sheets:** see `summer_flowers/console/`.

---

## Reading another app's doctype defensively — `1a6fcd4`, `de88d31`, `3065466` (2026-08-10)

Three commits, one mistake: this app read doctypes it does not own as though their
shape were fixed. It is not — a site that has a different mix of apps installed, or a
different version of one, has a different Bed, Farm and Crop Protocol Growth Stage.
Each of these was a `SELECT` on a column that was not there, or a mandatory column
left unwritten, and each took down a whole operation rather than degrading.

**`1a6fcd4` — Bed.custom_active.** `bed_rows()` selected `custom_active`
unconditionally. Bed belongs to `upande_propagation`, and an older copy has no such
column, so every Block and Bed read died with:

    MySQLdb.OperationalError: (1054, "Unknown column 'custom_active' in 'SELECT'")

Now asked for only when the meta has it; a row without it counts as active.

**`de88d31` — Crop Protocol Growth Stage.** `upande_agriculture` replaced the
`days_from`/`days_to`/`weeks` boundary trio with a single **mandatory**
`days_to_harvest`. `set_growth_stages` wrote the old three, so approving a protocol
threw `MandatoryError` on all six stages on any site carrying the newer doctype.
Now writes whichever columns the site has, with `days_to_harvest` taking the end of
the stage on its own clock.

**`3065466` — Farm.custom_location, custom_chemical_store, custom_fertilizer_store.**
Present on a bench only because `upande_kaitet` is installed there. Without it, asset
creation and Material Request defaulting both refused outright rather than falling
back, which is what both are written to do when a farm has none.

Found by diffing every foreign doctype between a bench with `upande_kaitet` and a site
without it. The rest of that diff is clean: `workflow_state` is only ever read off this
app's own plan, and `average_stem_length_cm` and `life_expectancy_years` are written to
Crop Protocol rather than selected from it, so a site without them drops the value
instead of breaking.

**Known gap, not fixed here.** `Crop Protocol.colour` carries
`"fetch_from": "variety.custom_color"`, but `Item.custom_color` is shipped by
`upande_kaitet`. On a site without it every Crop Protocol insert dies in
`get_invalid_links`. That belongs to `upande_agriculture` — which reads
`Item.custom_color` in `setup/build_crop_protocols.py` too without shipping it. Until
it is fixed there, a Property Setter clearing `fetch_from` on `Crop Protocol-colour`
unblocks the site; the field is an optional cosmetic Link to `Color`.

---

## 2026-08-10 — Motherstock in rounds, per-tab filters, financial year

The motherstock model stopped being a single arrival and became what the farm
actually does — a build-up in rounds, each with its own ramp and 52-week life.

- **Build-up rounds** `4effca2` — the pool arrives in N tranches, one per
  establishment round, rather than all at once. `d3780a0` made changing the number of
  rounds move the order date and the TC quantity together: fewer rounds is less time
  multiplying, so the order is later and larger.
- **Confirm a TC choice and have it stick** `243e26d` — a confirmed order now holds on
  the propagation plan, and can no longer leak onto another plan's.
- **Effect before committing** `d3780a0` — any change to quantity, order date or
  rounds, and the absence of a change, shows demand against production before it is
  saved.
- **The plan as its plantings** `230a2bb`, `6aee41d` — the planting plan reads as
  planting one to last with uprooting week, plants and area, not as blocks.
  `7af38ae` renamed plant week to planting week and dropped sticking, pinch and first
  harvest.
- **Financial year, everywhere** `0b8e5ff`, `7dfd295`, `c429591` — each tab now carries
  the filters it can answer to, built from the data it ranges over rather than from
  the one record on screen. The financial year reaches the chain summary and the
  process overview, not just the plan header.
- **Charts** `38e289e` demand as a line by financial year; `ce66f1f` demand against
  production, and the cumulative chart removed.
- **Demand is demand** `5554f90`, `fac6424` — production and variance came off the
  demand register, F1 came off the growing cycle, and the register gained
  extend-horizon, new-demand and create-a-propagation-plan.
- **Dates** `aedc75d` — every date on the dashboard now renders through the site's own
  date format instead of ISO.
- **Demand import** `268bb15` — `demand_import.parse/check/preview/import_demand`
  reads the three-year demand workbook.
- **Deploy diagnostics** `b88eac2`, `ed3a597` — see *Operational notes* below.

## 2026-08-07 — The cycles govern the TC order

- **Four cycles is four plants, not five** `06a340b` — `multiplication_factor` became
  `cycles × factor_per_cycle`. Buying 4,000 over 4 cycles orders 1,000 TC.
  `lead_time_for_cycles` scales the establishment lead time with the rounds.
- **Tweak and confirm** `f0db444` — `plan_whatif` accepts an order date and a cycle
  count, and `confirm_tc_choice` persists the choice downstream to the propagation
  plan and the motherstock batch. The same three levers are editable on the doctype
  and on the dashboard.
- **Supplied beside grown** `370d22b` — the week-by-week table shows what the order
  supplies next to what the plan grows.
- **Sourcing follows approval** `3349bb3` — approving a plan carries the sourcing all
  the way to the batch.
- **Fixes** `3424480` a hidden tab was taking fifteen fields with it; `66edd1a` both
  Create-plan buttons; `58195dd` the allocation bar was stealing a filter's element id.

## 2026-08-05 – 2026-08-06 — Beds, blocks and area become real

- **One area, and it is the bed** `db1aad9`, `4623c92` — gross area removed entirely;
  everything works in net area. `6a347e5` backfilled `bed_area` and made a bed belong
  to a block or a greenhouse, never both.
- **Allocate, don't decide** `6013554`, `ea653be`, `65cde28` — the plan is built from
  the demand first and blocks assigned afterwards; allocation suggests, and a block
  can no longer overstate its free beds.
- **Farm-owned blocks** `aef66e7` — a block and its beds may belong to a farm rather
  than a greenhouse.
- **TC arithmetic** `446870e` size from the planned peak; `1bbff35` order the
  requirement plus its loss allowance; `70cdea3`, `39895cf` stopped applying the
  cutting loss twice; `85885c9` the TC journey and the order-by date now come from one
  piece of arithmetic.
- **Propagation** `a4e275a`, `787621f` — cuttings are raised for every proposed
  planting, and there is one propagation plan per variety per season, updated in place.
- **Install** `f270e82` — stopped shipping another app's Document Link, which broke a
  fresh install with `Unknown column 'custom_material_request'`.

## 2026-08-03 – 2026-08-04 — The protocol gets one home; the season is July–June

- **Crop Protocol is the only place a protocol is edited** `d828590`, `3fb6aa8`,
  `fa69221`, `28dd419` — growth stages, the length distribution and the flush schedule
  all belong to the protocol, and mirroring into other tables stopped.
- **Approval writes the version** `bc25acd`, `2f80a12` — approving a Crop Protocol
  through its workflow is now the only thing that creates a Crop Protocol Version.
  `75eff7c` names protocols per variety and farm.
- **Seasons** `33abdbe`, `5b24fe7` — planning runs one growing season at a time, 1 July
  to 30 June, and the dashboard filters by season rather than calendar year.
- **Demand is per variety** `9721561`, `1adde57` — one demand register per variety; the
  farm belongs to the plan.
- **Ramp** `d5df14b` — capacity ramps to full instead of assuming 100% from week one.
- **Levers on the plan** `ade7775`, `4831068` — the TC quantity drives production, and
  the effect of moving a lever is shown before it is committed.

## 2026-08-01 – 2026-08-02 — The chain closes

- **Demand → production plan → propagation plan → motherstock → TC** `771b015` — the
  whole chain runs end to end, and `6c94b3f` ran a second variety through it.
- **Crop cycle dashboard** `866ce16` — planning and management for both scopes.
- **Planner constraints** `bd0fa7c` — minimum planting size and real block availability
  are respected.
- **Calendar and timeline** `b28f7b1`, `5df85d2` — a month calendar, a legible timeline,
  a Propagation tab, and year/week filters.

## 2026-07-29 – 2026-07-31 — First build

- **The app** `ee92eaf` — demand-driven planning, budgeting and motherstock.
- **Consolidation onto Crop Protocol** `7389d96` — versioned, per-farm, Farm Manager
  approved; `ee99456` put the Planting Calendar on Block and retired the parallel
  doctypes.
- **Motherstock simulation** `96f2ee8`, `ac2428b` — build-up with split and
  compounding, rebuilt on the weekly lifecycle with propagation feedback.
- **Planning dashboard** `8a443a6`, `c7b625d`, `70d9171` — demand to TC order with a
  build-up what-if, the planting plan, then tabs, block forecast and calendar.
- **Beds are real** `66d6382` — allocated per planting, and one bed can be uprooted.
- **Every derived figure explains itself** `ecd330a` — the numbers are clickable.
- **Budgets and inputs** `96708e5` — per-block budgets with live variance, input order
  sheets, block split and recombine. `9c69b42` then retired Input Order Sheet:
  Material Request is the input order sheet.
- **Biological assets** `943de5c` — cycle scope, resumable part-bed work, and real
  ERPNext Assets.
- **Workspace** `6ca7d25`, `5252a85`, `80b746b`, `02b9de3` — shipped as a fixture, with
  a Workspace Sidebar entry and linkable dashboard tabs.

---

## Operational notes on upgrade

**A malformed customization file will now refuse the migrate, by name.**
`before_migrate` runs `summer_flowers.customization_check.before_migrate` (`b88eac2`,
`ed3a597`). `frappe.modules.utils.sync_customizations` reads `doctype`,
`custom_fields` and `property_setters` out of every `custom/*.json` with no check that
they are there, and dies on a `KeyError` naming no file. This hook walks the same
loops first, across every installed app, and throws with the path. It refuses only
faults that would have stopped the migrate anyway. `bench --site SITE check-customizations`
runs the same scan without migrating, and there is a read-only
`...customization_check.check` for the console.

This is how the marginpar migrate failure was traced to
`upande_agriculture/upande_agriculture/custom/stock_entry.json`, which had lost its
top-level `doctype` key.

**Dependencies are not declared.** `required_apps` is commented out (`64c4ab1`,
`b789f29`) so the app installs on a bare bench. It still expects ERPNext, and reads
doctypes owned by `upande_agriculture` and `upande_propagation` — Bed, Block,
Greenhouse, Crop Protocol, Crop Cycle. Those apps must be installed for the module to
do anything; nothing will stop the install if they are not.

**Other apps' doctypes are read defensively, and this has bitten three times.**
`f270e82` (a Document Link on another app's doctype), `3424480` (a `field_order`
assumption) and `1a6fcd4` (`custom_active` on Bed). When a site runs a different
version of `upande_propagation` or `upande_agriculture`, check these first.

**Retired doctypes.** Input Order Sheet (`9c69b42`) and the parallel calendar doctypes
(`ee99456`) were removed in favour of Material Request and Planting Calendar on Block.
Sites that carried data in them need it moved before upgrading past those commits.

**Fixtures shipped.** `role.json`, `workflow.json`, `workflow_state.json`,
`workflow_action_master.json`, `workspace_sidebar.json`. A migrate will overwrite local
edits to any of these.

**Fiscal years.** Planning assumes a 1 July – 30 June financial year. ERPNext refuses
overlapping Fiscal Years unless they are company-scoped, so a site set up on calendar
years needs its years continued in 12-month steps from the last covered date rather
than replaced.
