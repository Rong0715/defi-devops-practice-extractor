(function () {
  "use strict";
  const D = window.DATA;
  const ALL = D.protocols;
  // Headline statistics count one subject per protocol family (the latest version), so a
  // team with three versions in the sample is not counted three times. The toggle in the
  // header switches every chart to all versions as a robustness check.
  const HAS_HEAD = ALL.some(p => p.headline);
  let sample = HAS_HEAD ? "headline" : "all";
  let P = ALL;
  const applySample = () => { P = sample === "headline" ? ALL.filter(p => p.headline) : ALL; };
  const RANK = { absent: 0, mentioned: 1, configured: 2, runs_in_ci: 3 };
  const LV = ["absent", "mentioned", "configured", "runs_in_ci"];
  const LV_LABEL = { absent: "Not found", mentioned: "Mentioned", configured: "Configured", runs_in_ci: "Run in CI" };
  const STAGE_LABEL = {
    before_launch: "Before launch (D1–D4)", at_launch: "At launch (D5)", governance: "Governance (D6)",
    after_launch: "After launch (D8)", assurance: "Assurance (D9)", cross_cutting: "Cross-cutting (D10)",
  };
  const DIM_LABEL = {
    D1_build: "Build", D2_test: "Test", D3_static: "Static", D4_ci: "CI", D5_deploy: "Deploy",
    D6_upgrade: "Upgrade & gov", D8_monitor: "Monitor", D9_assurance: "Assurance", D10_docs: "Docs",
  };
  const VAR = Object.fromEntries(D.variables.map(v => [v.id, v]));
  // derived roll-ups duplicate their sources, so they stay out of per-protocol / per-stage shares
  const ROLLUP = new Set(["D3_static.any_analyzer", "D2_test.formal_any"]);
  const countable = v => v.type === "adoption" || v.type === "bool";

  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pct = x => (x == null ? "–" : Math.round(x * 100) + "%");

  // ---- core measures ----------------------------------------------------
  // true / false when the cell counts, null when not applicable / unknown / error / not binary
  function adopted(p, v, mode) {
    const c = p.vars[v.id];
    if (!c || c.s !== "ok") return null;
    if (v.type === "adoption") return RANK[c.v] >= (mode === "ci" ? 3 : 2);
    if (v.type === "bool") return c.v === true;
    return null;
  }
  // Wilson score interval (95%): honest error bars for a share of a few dozen protocols
  function wilson(k, n) {
    if (!n) return [null, null];
    const z = 1.96, p = k / n, d = 1 + z * z / n;
    const c = (p + z * z / (2 * n)) / d, h = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d;
    return [Math.max(0, c - h), Math.min(1, c + h)];
  }
  function share(ps, v, mode) {
    let n = 0, k = 0;
    ps.forEach(p => { const a = adopted(p, v, mode); if (a !== null) { n++; if (a) k++; } });
    const [lo, hi] = wilson(k, n);
    return { n, k, share: n ? k / n : null, lo, hi };
  }
  function levelCounts(ps, v) {
    const c = { absent: 0, mentioned: 0, configured: 0, runs_in_ci: 0, n: 0 };
    ps.forEach(p => { const x = p.vars[v.id]; if (x && x.s === "ok") { c[x.v]++; c.n++; } });
    return c;
  }
  function poolShare(p, vars) {
    let n = 0, k = 0;
    vars.forEach(v => { const a = adopted(p, v, "any"); if (a !== null) { n++; if (a) k++; } });
    return n ? { share: k / n, n, k } : null;
  }
  const dimVars = d => D.variables.filter(v => v.dim === d && countable(v) && !ROLLUP.has(v.id));
  const allVars = D.variables.filter(v => countable(v) && !ROLLUP.has(v.id));

  // ---- small builders ---------------------------------------------------
  function table(cols, rows) {
    return "<table><thead><tr>" + cols.map(c => `<th>${esc(c)}</th>`).join("") + "</tr></thead><tbody>" +
      rows.map(r => "<tr>" + r.map(c => `<td>${esc(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table>";
  }
  function legend(el, items) {
    $(el).innerHTML = items.map(([label, cls]) => `<span style="--sw:var(${cls})">${esc(label)}</span>`).join("");
  }
  const tipAttr = t => `data-tip="${esc(t)}" tabindex="0"`;
  // practice name that reveals how the extractor measures it
  const howTip = v => (v.measured ? ` data-tip="${esc(v.measured)}" tabindex="0"` : "");
  const named = v => `<span class="help"${howTip(v)}>${esc(v.label)}</span>`;

  // ---- tooltip ----------------------------------------------------------
  const tip = $("#tip");
  function showTip(e, t) {
    tip.textContent = t; tip.style.display = "block";
    const r = tip.getBoundingClientRect();
    const x = Math.min(window.innerWidth - r.width - 8, e.clientX + 12);
    tip.style.left = Math.max(8, x) + "px"; tip.style.top = Math.max(8, e.clientY + 14 > window.innerHeight - r.height ? e.clientY - r.height - 10 : e.clientY + 14) + "px";
  }
  document.addEventListener("mousemove", e => { const t = e.target.closest && e.target.closest("[data-tip]"); t ? showTip(e, t.dataset.tip) : (tip.style.display = "none"); });
  document.addEventListener("focusin", e => { const t = e.target.closest && e.target.closest("[data-tip]"); if (t) { const b = t.getBoundingClientRect(); showTip({ clientX: b.left, clientY: b.bottom }, t.dataset.tip); } });
  document.addEventListener("focusout", () => { tip.style.display = "none"; });

  // ---- header -----------------------------------------------------------
  function header() {
    const n = P.length, fams = new Set(ALL.map(p => p.parent || p.id)).size;
    // The caveat depends on whether accuracy was measured, never on how many
    // protocols there are: a bigger sample is not a validated one.
    const drafts = P.filter(p => p.label_status === "draft").length;
    const notes = [];
    if (!D.accuracy) notes.push("the extractor's accuracy has not been measured against hand labels");
    if (drafts) notes.push(`the upgradeable/immutable and launch-year labels are unreviewed drafts${drafts < n ? ` for ${drafts} of ${n} protocols` : ""}`);
    if (notes.length) {
      const b = $("#banner"); b.hidden = false;
      b.innerHTML = `<strong>Provisional results.</strong> Treat every number as unvalidated: ${notes.join(", and ")}. See Methods for how the ${ALL.length} protocols were sampled.`;
    }
    const an = share(P, VAR["D3_static.any_analyzer"], "ci");
    const au = share(P, VAR["D9_assurance.audited_publicly"], "any");
    const kpis = [
      [n, sample === "headline" ? `protocol families counted (${ALL.length} versions scanned)` : `protocol versions counted (${fams} families)`],
      [D.variables.filter(v => !v.derived).length, "practices measured"],
      [pct(an.share), `run a static analyzer in CI (n=${an.n})`],
      [pct(au.share), `have a public audit report (n=${au.n})`],
    ];
    $("#kpis").innerHTML = kpis.map(([a, b]) => `<div class="kpi"><div class="n">${esc(a)}</div><div class="t">${esc(b)}</div></div>`).join("");
    $("#sample-seg").hidden = !HAS_HEAD;
    document.querySelectorAll("#sample-seg button").forEach(b => {
      b.setAttribute("aria-pressed", b.dataset.sample === sample);
      b.textContent = b.dataset.sample === "headline" ? `one per family (n=${ALL.filter(p => p.headline).length})` : `every version (n=${ALL.length})`;
    });
    $("#foot").textContent = `Sample and TVL from the DefiLlama snapshot of ${D.snapshot_date}; repositories scanned ${String(D.generated_at || "").slice(0, 10)}. Generated from public repositories only; see Methods for what that does and does not show.`;
  }

  // ---- 1. ranking -------------------------------------------------------
  let mode = "any";
  function ranking() {
    const rows = D.variables.filter(countable).map(v => ({ v, ...share(P, v, mode) }))
      .filter(r => r.n > 0).sort((a, b) => b.share - a.share || b.n - a.n || a.v.label.localeCompare(b.v.label));
    $("#ranking-rows").innerHTML = rows.map(r => `
      <div class="row">
        <div class="lab">${named(r.v)}<small>${esc(DIM_LABEL[r.v.dim])}${r.v.type === "adoption" ? "" : " · yes/no"}</small></div>
        <div class="bar single ci" ${tipAttr(`${r.v.label}: ${r.k} of ${r.n} protocols (${pct(r.share)}); 95% interval ${pct(r.lo)}–${pct(r.hi)}`)}><i style="width:${Math.max(r.share * 100, r.k ? 1.5 : 0)}%"></i><b style="left:${r.lo * 100}%;width:${(r.hi - r.lo) * 100}%"></b></div>
        <div class="val">${pct(r.share)} <small>${r.k}/${r.n}</small></div>
      </div>`).join("");
    $("#ranking-tbl").innerHTML = table(["Practice", "Dimension", "Adopted", "Applicable (n)", "Share", "95% interval"],
      rows.map(r => [r.v.label, DIM_LABEL[r.v.dim], r.k, r.n, pct(r.share), `${pct(r.lo)}–${pct(r.hi)}`]));
  }
  document.querySelectorAll("#mode-seg button").forEach(b => b.addEventListener("click", () => {
    mode = b.dataset.mode;
    document.querySelectorAll("#mode-seg button").forEach(x => x.setAttribute("aria-pressed", x === b));
    ranking();
  }));

  // ---- 2. said vs run ---------------------------------------------------
  function saidVsRun() {
    legend("#lv-legend", [["Run in CI", "--lv3"], ["Configured", "--lv2"], ["Mentioned", "--lv1"], ["Not found", "--lv0"]]);
    const rows = D.variables.filter(v => v.type === "adoption").map(v => ({ v, c: levelCounts(P, v) }))
      .filter(r => r.c.n > 0)
      .sort((a, b) => (b.c.runs_in_ci / b.c.n) - (a.c.runs_in_ci / a.c.n) || (b.c.configured / b.c.n) - (a.c.configured / a.c.n));
    $("#svr-rows").innerHTML = rows.map(({ v, c }) => {
      const trace = c.n - c.absent;
      const seg = ["runs_in_ci", "configured", "mentioned", "absent"].map((k, i) => c[k] ?
        `<i class="lv${3 - i}" style="width:${c[k] / c.n * 100}%" ${tipAttr(`${v.label} · ${LV_LABEL[k]}: ${c[k]} of ${c.n}`)}></i>` : "").join("");
      const sub = trace ? `${c.runs_in_ci} of ${trace} with any trace run it in CI` : "no trace in any protocol";
      return `<div class="row"><div class="lab">${named(v)}<small>${sub}</small></div><div class="bar stack">${seg}</div><div class="val"><small>n=${c.n}</small></div></div>`;
    }).join("");
    $("#svr-tbl").innerHTML = table(["Practice", "n", "Run in CI", "Configured", "Mentioned", "Not found"],
      rows.map(({ v, c }) => [v.label, c.n, c.runs_in_ci, c.configured, c.mentioned, c.absent]));
  }

  // ---- 2b. practice ladder ----------------------------------------------
  // Do protocols adopt practices in one order? A ladder (Guttman scale) holds when a protocol
  // with a rarer practice also has the more common ones. Loevinger's H compares the observed
  // order violations with the number expected if practices were adopted independently:
  // 0 = no order, 1 = perfect ladder; 0.3 / 0.4 / 0.5 are the usual weak / medium / strong marks.
  const CORE = ["D2_test.suite_present", "D4_ci.runs_on_pull_request", "D2_test.fuzz_testing",
    "D2_test.invariant_testing", "D2_test.formal_any"];
  // single tools already counted by a roll-up (any analyzer, any formal tool)
  const ROLLED = v => /^D2_test\.formal_(?!any)|^D3_static\.(?!any_analyzer)/.test(v.id);
  function ladderCols(ps, vars, m) {
    const X = {};
    vars.forEach(v => {
      const col = ps.map(p => adopted(p, v, m)), k = col.filter(x => x).length, n = col.filter(x => x !== null).length;
      if (k > 0 && k < n) X[v.id] = { col, pop: k / n, k, n };
    });
    return X;
  }
  // [violations, violations expected under independence] for one pair of practices
  function pairErr(a, b) {
    let n = 0, na = 0, nb = 0, f = 0;
    const [e, h] = a.pop >= b.pop ? [a, b] : [b, a];
    for (let i = 0; i < e.col.length; i++) {
      const x = e.col[i], y = h.col[i];
      if (x === null || y === null) continue;
      n++; if (x) na++; if (y) nb++; if (!x && y) f++;
    }
    return n ? [f, (n - na) * nb / n] : [0, 0];
  }
  function scaleH(X, ids) {
    let F = 0, E = 0;
    for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) {
      const [f, e] = pairErr(X[ids[i]], X[ids[j]]); F += f; E += e;
    }
    return E ? 1 - F / E : null;
  }
  function itemH(X, id, ids) {
    let F = 0, E = 0;
    ids.forEach(j => { if (j !== id) { const [f, e] = pairErr(X[id], X[j]); F += f; E += e; } });
    return E ? 1 - F / E : null;
  }
  // 95% interval for H by resampling protocols; seeded so the page shows the same numbers every load
  function bootH(ps, ids, m) {
    let s = 20261005;
    const rnd = () => { s |= 0; s = s + 0x6D2B79F5 | 0; let t = Math.imul(s ^ s >>> 15, 1 | s); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
    const vars = ids.map(i => VAR[i]), out = [];
    for (let b = 0; b < 1000; b++) {
      const X = ladderCols(ps.map(() => ps[Math.floor(rnd() * ps.length)]), vars, m);
      if (ids.every(i => X[i])) { const h = scaleH(X, ids); if (h !== null) out.push(h); }
    }
    out.sort((a, b) => a - b);
    return out.length ? [out[Math.floor(out.length * .025)], out[Math.floor(out.length * .975)]] : [null, null];
  }
  // rungs in the current sample's order (most common first) and each protocol's place on them
  function ladderFit(ps) {
    const X = ladderCols(ps, CORE.map(i => VAR[i]), "any");
    const rungs = CORE.filter(i => X[i]).sort((a, b) => X[b].pop - X[a].pop || CORE.indexOf(a) - CORE.indexOf(b));
    const fit = {};
    ps.forEach((p, k) => {
      const has = rungs.map(i => X[i].col[k] === true), n = has.filter(Boolean).length;
      fit[p.id] = { has, n, on: has.every((h, i) => h === (i < n)) };
    });
    return { X, rungs, fit };
  }
  const f2 = x => (x == null ? "–" : (x < 0 ? "−" : "") + Math.abs(x).toFixed(2));
  const strength = h => h == null ? "" : h >= .5 ? "strong" : h >= .4 ? "medium" : h >= .3 ? "weak" : "none";
  let LAD = null;
  function ladder() {
    const vars = D.variables.filter(v => countable(v) && !ROLLED(v));
    const XA = ladderCols(P, vars, "any"), allIds = Object.keys(XA);
    const { X, rungs, fit } = LAD = ladderFit(P);
    const hAll = scaleH(XA, allIds), hCore = scaleH(X, rungs), [lo, hi] = bootH(P, rungs, "any");
    const XC = ladderCols(P, CORE.map(i => VAR[i]), "ci"), hCi = scaleH(XC, CORE.filter(i => XC[i]));
    const on = P.filter(p => fit[p.id].on).length;
    $("#ladder-kpis").innerHTML = `
      <div class="kpi"><div class="n">${f2(hAll)}</div><div class="t">scalability (H) of all ${allIds.length} practices together: ${hAll >= .3 ? "a " + strength(hAll) + " ladder" : "below 0.3, so no single ladder"}</div></div>
      <div class="kpi"><div class="n">${f2(hCore)}</div><div class="t">H of the ${rungs.length}-rung testing and CI ladder (95% interval ${f2(lo)} to ${f2(hi)}): a ${strength(hCore)} ladder</div></div>
      <div class="kpi"><div class="n">${on}<small> of ${P.length}</small></div><div class="t">protocols sit exactly on that ladder, with no rung skipped</div></div>`;

    // staircase: one column per protocol, most rungs first; cells that break the order stand out
    const ps = P.slice().sort((a, b) => fit[b.id].n - fit[a.id].n || fit[b.id].on - fit[a.id].on || a.name.localeCompare(b.name));
    legend("#ladder-legend", [["Found, in order", "--lv2"], ["Found, but a lower rung is missing", "--c2"], ["Not found", "--track"]]);
    $("#ladder-stairs").innerHTML = `<div class="stairs" style="--n:${ps.length}">` + rungs.map((id, r) =>
      `<div class="lab">${named(VAR[id])}<small>${X[id].k} of ${X[id].n}</small></div><div class="strip">` + ps.map(p => {
        const f = fit[p.id], h = f.has[r], odd = h && r >= f.n, skip = !h && r < f.n;
        return `<i class="${h ? (odd ? "odd" : "has") : skip ? "skip" : ""}" data-tip="${esc(`${p.name}: ${VAR[id].label} ${h ? "found" : "not found"}${odd ? ", although a more common rung is missing" : skip ? ", although a rarer rung is present" : ""}`)}"></i>`;
      }).join("") + "</div>").join("") + "</div>";
    const tied = rungs.slice(1).filter((id, i) => Math.abs(X[rungs[i]].k - X[id].k) <= 3).map(id => `${VAR[rungs[rungs.indexOf(id) - 1]].label} and ${VAR[id].label}`);
    $("#ladder-note").innerHTML = `Each column is one protocol, sorted by how many rungs it has. A perfect ladder would be a clean staircase. ` +
      (tied.length ? `${esc(tied.join("; "))} are almost equally common, so their order is not settled; most breaks are a swap of those two. ` : "") +
      `Counting only what runs in CI gives H = ${f2(hCi)}, but that is partly circular (a tool cannot run in CI without CI), so the looser “configured or better” threshold is used here.`;

    // rung counts
    const counts = rungs.map(() => 0).concat(0); P.forEach(p => { if (fit[p.id].on) counts[fit[p.id].n]++; });
    const rungName = k => k === 0 ? "None of the rungs" : `Up to: ${VAR[rungs[k - 1]].label}`;
    const mx = Math.max(...counts, P.length - on, 1);
    $("#ladder-rungs").innerHTML = counts.map((c, k) => [`Rung ${k}`, rungName(k), c]).concat([["Off the ladder", "at least one rung out of order", P.length - on]])
      .map(([a, b, c]) => `<div class="row"><div class="lab">${esc(a)}<small>${esc(b)}</small></div><div class="bar single" ${tipAttr(`${a} (${b}): ${c} of ${P.length} protocols`)}><i style="width:${c / mx * 100}%"></i></div><div class="val">${c} <small>${pct(c / P.length)}</small></div></div>`).join("");

    // every other practice against the ladder
    const others = allIds.filter(i => !rungs.includes(i)).map(i => {
      const XX = Object.assign({ [i]: XA[i] }, X); return { v: VAR[i], h: itemH(XX, i, rungs), k: XA[i].k, n: XA[i].n };
    }).sort((a, b) => b.h - a.h);
    $("#ladder-others").innerHTML = others.map(r => `
      <div class="row"><div class="lab">${named(r.v)}<small>${esc(DIM_LABEL[r.v.dim])} · ${r.k} of ${r.n}${r.k < 5 ? " · too rare to judge" : ""}</small></div>
        <div class="bar single mark${r.k < 5 ? " faint" : ""}" ${tipAttr(`${r.v.label}: H = ${f2(r.h)} against the ladder; found in ${r.k} of ${r.n} protocols`)}><i style="width:${Math.max(0, r.h) * 100}%"></i><b style="left:30%"></b></div>
        <div class="val">${f2(r.h)}</div></div>`).join("");
    $("#ladder-tbl").innerHTML = table(["Protocol", "Rungs found", "On the ladder", ...rungs.map(i => VAR[i].label)],
      ps.map(p => [p.name, fit[p.id].n, fit[p.id].on ? "yes" : "no", ...fit[p.id].has.map(h => h ? "found" : "–")])) +
      table(["Other practice", "Dimension", "Found in", "H against the ladder"], others.map(r => [r.v.label, DIM_LABEL[r.v.dim], `${r.k}/${r.n}`, f2(r.h)]));
  }

  // ---- 3. stage profile -------------------------------------------------
  function stages() {
    const rows = D.stages.map(s => {
      const vars = D.variables.filter(v => v.stage === s && countable(v) && !ROLLUP.has(v.id));
      const per = P.map(p => poolShare(p, vars)).filter(Boolean);
      const mean = per.length ? per.reduce((a, x) => a + x.share, 0) / per.length : null;
      return { s, vars, mean, per };
    }).filter(r => r.mean !== null);
    $("#stage-rows").innerHTML = rows.map(r => {
      const lo = Math.min(...r.per.map(x => x.share)), hi = Math.max(...r.per.map(x => x.share));
      return `<div class="row"><div class="lab">${esc(STAGE_LABEL[r.s])}<small>${r.vars.length} practices</small></div>
        <div class="bar single" ${tipAttr(`${STAGE_LABEL[r.s]}: mean ${pct(r.mean)}; protocols range ${pct(lo)}–${pct(hi)}`)}><i style="width:${Math.max(r.mean * 100, 1.5)}%"></i></div>
        <div class="val">${pct(r.mean)}</div></div>`;
    }).join("");
    $("#stage-tbl").innerHTML = table(["Stage", "Practices", "Mean share", "Lowest protocol", "Highest protocol"],
      rows.map(r => [STAGE_LABEL[r.s], r.vars.length, pct(r.mean), pct(Math.min(...r.per.map(x => x.share))), pct(Math.max(...r.per.map(x => x.share)))]));
  }

  // ---- 4. upgradeable vs immutable -------------------------------------
  function upgradeability() {
    const groups = [["immutable", "Immutable", "g1", "--c1"], ["upgradeable", "Upgradeable", "g2", "--c2"], ["mixed", "Mixed", "g3", "--c3"]]
      .map(([k, l, cls, css]) => ({ k, l, cls, css, ps: P.filter(p => p.upgradeability === k) })).filter(g => g.ps.length);
    legend("#up-legend", groups.map(g => [`${g.l} (n=${g.ps.length})`, g.css]));
    const ids = ["D2_test.formal_any", "D2_test.fuzz_testing", "D2_test.invariant_testing", "D2_test.coverage",
      "D3_static.any_analyzer", "D4_ci.lint_gate", "D5_deploy.onchain_state_verification", "D6_upgrade.upgrade_safety_check",
      "D6_upgrade.proposal_simulation", "D8_monitor.security_policy", "D8_monitor.bug_bounty", "D9_assurance.audited_publicly", "D9_assurance.competitive_audit"];
    const trows = [];
    $("#up-rows").innerHTML = ids.map(id => {
      const v = VAR[id];
      const lines = groups.map(g => {
        const s = share(g.ps, v, "any");
        trows.push([v.label, g.l, s.n ? `${s.k}/${s.n}` : "n/a", pct(s.share)]);
        const bar = s.n ? `<div class="bar single" ${tipAttr(`${v.label} · ${g.l}: ${s.k} of ${s.n}`)}><i class="${g.cls}" style="width:${Math.max(s.share * 100, s.k ? 1.5 : 0)}%"></i></div>` : `<div class="bar"></div>`;
        return `<div class="row"><div class="lab"></div>${bar}<div class="val">${s.n ? pct(s.share) + " <small>" + s.k + "/" + s.n + "</small>" : "<small>n/a</small>"}</div></div>`;
      }).join("");
      return `<div class="groupblock"><div class="row" style="grid-template-columns:1fr"><div class="lab"><strong>${named(v)}</strong></div></div><div class="pair">${lines}</div></div>`;
    }).join("");
    const small = groups.filter(g => g.ps.length < 8);
    $("#up-note").textContent = (small.length ? `Groups with fewer than 8 protocols (${small.map(g => `${g.l.toLowerCase()} n=${g.ps.length}`).join(", ")}) are too small to compare. ` : "") +
      "Upgrade-safety checks are not applicable to immutable protocols and are excluded from their share.";
    $("#up-tbl").innerHTML = table(["Practice", "Group", "Adopted", "Share"], trows);
  }

  // ---- 5. toolchain by launch cohort -----------------------------------
  const cohortOf = y => (y == null ? null : y <= 2020 ? "≤2020" : y <= 2022 ? "2021–22" : "2023+");
  function toolchain() {
    const cohorts = ["≤2020", "2021–22", "2023+"].map(c => ({ c, ps: P.filter(p => cohortOf(p.launch_year) === c) })).filter(x => x.ps.length);
    const fwOf = p => { const c = p.vars["D1_build.framework"]; if (!c || c.s !== "ok") return null; return c.v === "foundry" ? "Foundry" : c.v === "hardhat" ? "Hardhat" : c.v === "foundry+hardhat" ? "Both" : "Other / none"; };
    const fwCls = { Foundry: "g1", Hardhat: "g2", Both: "g3", "Other / none": "go" };
    legend("#fw-legend", [["Foundry", "--c1"], ["Hardhat", "--c2"], ["Foundry + Hardhat", "--c3"], ["Other / none", "--other"]]);
    const trows = [];
    $("#fw-rows").innerHTML = cohorts.map(({ c, ps }) => {
      const cnt = {}; ps.forEach(p => { const f = fwOf(p); if (f) cnt[f] = (cnt[f] || 0) + 1; });
      const n = Object.values(cnt).reduce((a, b) => a + b, 0);
      const seg = Object.keys(fwCls).filter(k => cnt[k]).map(k => `<i class="${fwCls[k]}" style="width:${cnt[k] / n * 100}%" ${tipAttr(`${c} · ${k}: ${cnt[k]} of ${n}`)}></i>`).join("");
      Object.keys(fwCls).forEach(k => trows.push([c, "Framework: " + k, cnt[k] || 0, n]));
      return `<div class="row"><div class="lab">Launched ${esc(c)}<small>n=${n}</small></div><div class="bar stack">${seg}</div><div class="val"></div></div>`;
    }).join("");
    legend("#co-legend", cohorts.map((x, i) => [`Launched ${x.c} (n=${x.ps.length})`, `--lv${i + 1}`]));
    const ids = ["D2_test.fuzz_testing", "D2_test.invariant_testing", "D3_static.any_analyzer", "D2_test.formal_any", "D2_test.coverage"];
    $("#co-rows").innerHTML = ids.map(id => {
      const v = VAR[id];
      const lines = cohorts.map((x, i) => {
        const s = share(x.ps, v, "any"); trows.push([x.c, v.label + " (configured or better)", s.k, s.n]);
        return `<div class="row"><div class="lab"></div><div class="bar single" ${tipAttr(`${v.label} · ${x.c}: ${s.k} of ${s.n}`)}><i style="background:var(--lv${i + 1});width:${s.n ? Math.max(s.share * 100, s.k ? 1.5 : 0) : 0}%"></i></div><div class="val">${pct(s.share)} <small>${s.k}/${s.n}</small></div></div>`;
      }).join("");
      return `<div class="groupblock"><div class="row" style="grid-template-columns:1fr"><div class="lab"><strong>${named(v)}</strong></div></div><div class="pair">${lines}</div></div>`;
    }).join("");
    const nol = P.filter(p => p.launch_year == null).length;
    if (nol) $("#co-rows").insertAdjacentHTML("beforeend", `<p class="note">${nol} protocol(s) without a launch year are omitted here.</p>`);
    $("#tc-tbl").innerHTML = table(["Cohort", "Measure", "Count", "n"], trows);
  }

  // ---- 6. heatmap explorer ---------------------------------------------
  const bucket = s => (s == null ? "na" : s === 0 ? "" : s <= .2 ? "h1" : s <= .4 ? "h2" : s <= .6 ? "h3" : s <= .8 ? "h4" : "h5");
  const heatCols = () => D.dims.filter(d => dimVars(d.id).length);
  const hstate = { q: "", up: "", repo: "", sort: ALL.some(p => p.tvl) ? "tvl" : "name", sel: null };
  // Is the scanned repo the protocol's real code? (tools/verify_mapping.py, or a person)
  const RC = {
    auto: ["✓", "Repo confirmed: it declares contracts the protocol has deployed on Ethereum"],
    hand: ["✓", "Repo confirmed by hand"],
    doubtful: ["!", "Repo flagged as doubtful by hand"],
    unconfirmed: ["?", "Repo not yet tied to deployed contracts"],
  };
  const rcOk = p => p.repo_check && (p.repo_check.status === "auto" || p.repo_check.status === "hand");
  function rcText(p) {
    const c = p.repo_check; if (!c) return "";
    const d = c.status === "auto" ? c.detail.split(";").slice(0, 3).join(", ") : c.detail;
    return RC[c.status][1] + (d ? ` (${d})` : "");
  }
  const badge = p => p.repo_check
    ? `<span class="rc ${p.repo_check.status}" data-tip="${esc(rcText(p))}" aria-hidden="true">${RC[p.repo_check.status][0]}</span><span class="sr">${esc(RC[p.repo_check.status][1])}</span>` : "";
  const overall = p => { const s = poolShare(p, allVars); return s ? s.share : -1; };

  function heatRows() {
    const q = hstate.q.trim().toLowerCase();
    let ps = P.filter(p => (!q || p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q))
      && (!hstate.up || p.upgradeability === hstate.up)
      && (!hstate.repo || (hstate.repo === "ok") === rcOk(p)));
    const byName = (a, b) => a.name.localeCompare(b.name);
    if (hstate.sort === "name") ps.sort(byName);
    else if (hstate.sort === "tvl") ps.sort((a, b) => (b.tvl || 0) - (a.tvl || 0) || byName(a, b));
    else if (hstate.sort === "year") ps.sort((a, b) => (a.launch_year || 9999) - (b.launch_year || 9999) || byName(a, b));
    else if (hstate.sort === "overall") ps.sort((a, b) => overall(b) - overall(a) || byName(a, b));
    else if (hstate.sort === "ladder") {
      const f = ladderFit(P).fit;
      ps.sort((a, b) => f[b.id].n - f[a.id].n || f[b.id].on - f[a.id].on || overall(b) - overall(a) || byName(a, b));
    }
    else if (hstate.sort === "category") ps.sort((a, b) => (a.category || "~").localeCompare(b.category || "~") || overall(b) - overall(a) || byName(a, b));
    else if (hstate.sort === "family") {
      // families by their best TVL, versions inside a family newest first
      const top = {}; ps.forEach(p => { const f = p.parent || p.id; top[f] = Math.max(top[f] || 0, p.tvl || 0); });
      const fam = p => p.parent || p.id;
      ps.sort((a, b) => top[fam(b)] - top[fam(a)] || fam(a).localeCompare(fam(b)) || (b.launch_year || 0) - (a.launch_year || 0) || byName(b, a));
    }
    else if (hstate.sort === "commit") ps.sort((a, b) => (b.commit_date || "").localeCompare(a.commit_date || "") || byName(a, b));
    else {
      const sc = p => { const s = poolShare(p, dimVars(hstate.sort)); return s ? s.share : -1; };
      ps.sort((a, b) => sc(b) - sc(a) || byName(a, b));
    }
    return ps;
  }

  function renderHeat() {
    const cols = heatCols(), ps = heatRows();
    const sortLab = SORTS[hstate.sort] || DIM_LABEL[hstate.sort] + " score";
    const head = "<tr><th></th>" + cols.map(d => {
      const on = hstate.sort === d.id;
      return `<th ${on ? 'aria-sort="descending"' : ""} title="${esc(STAGE_LABEL[d.stage])}">` +
        `<button type="button" class="sorth${on ? " on" : ""}" data-sort="${d.id}" ` +
        `aria-label="Sort by ${esc(DIM_LABEL[d.id])}">${esc(d.id.split("_")[0])}` +
        `<span>${esc(DIM_LABEL[d.id])}${on ? " ▾" : ""}</span></button></th>`;
    }).join("") + "</tr>";
    const sub = p => hstate.sort === "ladder" ? (f => `${f.n} of ${f.has.length} rungs${f.on ? "" : ", out of order"}`)(LAD.fit[p.id]) : hstate.sort === "category" ? p.category : hstate.sort === "commit" ? p.commit_date : hstate.sort === "overall" ? pct(overall(p)) : hstate.sort === "year" || hstate.sort === "family" ? p.launch_year : "";
    const body = ps.map(p => `<tr><th class="rowh" scope="row"><button type="button" data-p="${esc(p.id)}">${esc(p.name)}${badge(p)}${sub(p) ? `<small>${esc(sub(p))}</small>` : ""}</button></th>` +
      cols.map(d => {
        const s = poolShare(p, dimVars(d.id));
        const t = s ? `${p.name} · ${DIM_LABEL[d.id]}: ${s.k} of ${s.n} applicable practices found`
          : `${p.name} · ${DIM_LABEL[d.id]}: nothing applicable`;
        const on = hstate.sel && hstate.sel.p === p.id && hstate.sel.d === d.id;
        return `<td><button type="button" class="cell ${bucket(s && s.share)}" data-p="${esc(p.id)}" data-d="${d.id}" ` +
          `aria-expanded="${on}" aria-label="${esc(t)}" data-tip="${esc(t)}">${s ? Math.round(s.share * 100) : "–"}</button></td>`;
      }).join("") + "</tr>").join("");
    $("#heat").innerHTML = ps.length
      ? `<table><thead>${head}</thead><tbody>${body}</tbody></table>`
      : `<p class="note">No protocol matches those filters.</p>`;
    $("#heat-count").textContent = `${ps.length} of ${P.length} protocols, by ${sortLab}`;
    // legend counts follow the current sample (one per family / every version)
    $("#heat-rc").innerHTML = ["auto", "hand", "unconfirmed", "doubtful"]
      .map(k => [k, P.filter(p => p.repo_check && p.repo_check.status === k).length]).filter(([, n]) => n)
      .map(([k, n]) => `<span><span class="rc ${k}" aria-hidden="true">${RC[k][0]}</span>${esc(RC[k][1].replace("Repo ", "repo "))} (${n})</span>`).join("");
  }

  const SORTS = { tvl: "Ethereum TVL", overall: "overall score", ladder: "testing and CI ladder", name: "name", year: "launch year",
    category: "category", family: "protocol family", commit: "most recent commit" };
  function heatmap() {
    $("#heat-up").innerHTML = `<option value="">all protocols</option>` +
      ["immutable", "upgradeable", "mixed"].filter(u => ALL.some(p => p.upgradeability === u))
        .map(u => `<option value="${u}">${u} only</option>`).join("");
    const has = { tvl: ALL.some(p => p.tvl), year: ALL.some(p => p.launch_year), category: ALL.some(p => p.category),
      commit: ALL.some(p => p.commit_date), family: ALL.some(p => p.parent) };
    $("#heat-sort").innerHTML =
      Object.entries(SORTS).filter(([k]) => has[k] !== false).map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("") +
      heatCols().map(d => `<option value="${d.id}">${esc(DIM_LABEL[d.id])} score</option>`).join("");
    $("#heat-sort").value = hstate.sort;
    $("#heat-scale").innerHTML = `<span>share of applicable practices found:</span>` +
      ["--hm0", "--hm1", "--hm2", "--hm3", "--hm4", "--hm5"].map(c => `<i style="background:var(${c})"></i>`).join("") +
      `<span>0% → 100%</span><i style="background:repeating-linear-gradient(45deg,var(--track) 0 3px,var(--surface) 3px 6px)"></i><span>nothing applicable</span>`;

    const sync = () => { hstate.sort = $("#heat-sort").value; renderHeat(); };
    let t;
    $("#heat-q").addEventListener("input", e => {
      clearTimeout(t); t = setTimeout(() => { hstate.q = e.target.value; renderHeat(); }, 120);
    });
    $("#heat-up").addEventListener("change", e => { hstate.up = e.target.value; renderHeat(); });
    const hasRC = ALL.some(p => p.repo_check);
    $("#heat-repo-l").hidden = $("#heat-repo").hidden = !hasRC;
    $("#heat-repo").innerHTML = `<option value="">any repo check</option><option value="ok">repo confirmed</option><option value="no">repo not yet confirmed</option>`;
    $("#heat-repo").addEventListener("change", e => { hstate.repo = e.target.value; renderHeat(); });

    $("#heat-sort").addEventListener("change", sync);
    $("#heat-reset").addEventListener("click", () => {
      hstate.q = ""; hstate.up = ""; hstate.repo = ""; hstate.sort = ALL.some(p => p.tvl) ? "tvl" : "name";
      $("#heat-q").value = ""; $("#heat-up").value = ""; $("#heat-repo").value = ""; $("#heat-sort").value = hstate.sort;
      renderHeat();
    });
    $("#heat").addEventListener("click", e => {
      const s = e.target.closest("button[data-sort]");
      if (s) { hstate.sort = s.dataset.sort; $("#heat-sort").value = hstate.sort; renderHeat(); return; }
      const b = e.target.closest("button[data-p]"); if (!b) return;
      hstate.sel = b.classList.contains("cell") ? { p: b.dataset.p, d: b.dataset.d } : null;
      document.querySelectorAll(".cell[aria-expanded=true]").forEach(x => x.setAttribute("aria-expanded", "false"));
      if (b.classList.contains("cell")) b.setAttribute("aria-expanded", "true");
      detail(b.dataset.p, b.dataset.d || null);
    });
    renderHeat();
  }

  function valuePill(v, c) {
    if (c.s !== "ok") return `<span class="pill na">${c.s === "na" ? "not applicable" : c.s}</span>`;
    if (v.type === "adoption") return `<span class="pill lv${RANK[c.v]}">${LV_LABEL[c.v]}</span>`;
    if (v.type === "bool") return `<span class="pill ${c.v ? "lv2" : ""}">${c.v ? "yes" : "no"}</span>`;
    if (c.v === "none" || c.v == null) return `<span class="pill">none</span>`;
    return `<span class="pill">${esc(String(c.v).replace(/[-_]/g, " "))}</span>`;
  }
  function evidence(c) {
    if (c.s !== "ok") {
      const why = { immutable: "the protocol is immutable", no_admin_role: "the protocol has no admin role", no_solidity: "no Solidity in the scanned repos", no_source: "no source files found" }[c.reason];
      const gap = c.reason && c.reason.startsWith("scope_gap:") ? `Not found in the scanned repos, and part of this protocol lives elsewhere (${c.reason.slice(10)}).` : "";
      return `<div class="ev"><span class="flag">${esc(c.s === "na" ? "Excluded from rates: " + (why || c.reason) : gap || c.reason || "")}</span></div>`;
    }
    if (!c.ev.length) return `<div class="ev"><span class="flag">No evidence found in the scanned repositories.</span></div>`;
    return `<div class="ev">` + c.ev.slice(0, 3).map(e => {
      const where = e.path ? `${e.path}${e.line ? ":" + e.line : ""}` : (e.snippet || "");
      const link = e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener"><code>${esc(where)}</code></a>` : `<code>${esc(where)}</code>`;
      const extra = [e.via ? `via ${esc(e.via)}` : "", e.triggers ? `on ${esc(e.triggers.join(", "))}` : "",
        e.external_workflow ? "reusable workflow in another repo, inferred from its name" : "", e.advisory ? "advisory: failure does not fail the build" : "", e.by_filename ? "matched on the workflow's name, not a command" : ""].filter(Boolean).join(" · ");
      const snip = e.path && e.snippet && !e.by_filename ? `<div>“${esc(e.snippet.slice(0, 110))}”</div>` : "";
      return `<div>${link}${extra ? ` <span class="flag">${extra}</span>` : ""}${snip}</div>`;
    }).join("") + `</div>`;
  }
  function detail(pid, dim) {
    const p = ALL.find(x => x.id === pid);
    const dims = dim ? [dim] : D.dims.map(d => d.id);
    const repos = p.repos.map(r => `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.url.replace("https://github.com/", ""))}</a> @ ${esc((r.commit || "").slice(0, 10))} (${esc(r.role)})`).join(" · ");
    const sibs = ALL.filter(x => x.parent && x.parent === p.parent && x.id !== p.id).map(x => x.name);
    const ctx = [p.category, p.launch_year && "launched " + p.launch_year, p.upgradeability,
      p.commit_date && "last commit " + p.commit_date,
      rcText(p),
      sibs.length ? (p.headline ? "counts for its family" : "not counted in one-per-family totals") + " (also scanned: " + sibs.join(", ") + ")" : "",
      p.label_status === "draft" ? "context labels are drafts" : ""].filter(Boolean).join(" · ");
    $("#detail").innerHTML = `<div class="detail"><h3>${esc(p.name)}${dim ? " — " + esc(DIM_LABEL[dim]) : ""}</h3>
      <p class="meta">${esc(ctx)}<br>Scanned: ${repos}</p>` +
      dims.map(d => D.variables.filter(v => v.dim === d).map(v => {
        const c = p.vars[v.id]; if (!c) return "";
        return `<div class="vrow"><div>${named(v)}${dim ? "" : `<br><small style="color:var(--ink-3)">${esc(DIM_LABEL[v.dim])}</small>`}</div><div>${valuePill(v, c)}</div>${evidence(c)}</div>`;
      }).join("")).join("") + `</div>`;
    $("#detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // ---- 7. methods -------------------------------------------------------
  function methods() {
    const n = P.length, pilot = !D.accuracy;
    const acc = D.accuracy;
    const accHtml = acc ? table(["Practice", "Labelled protocols", "Accuracy", "Precision", "Recall", "Status"],
      acc.map(a => [a.label, a.n, pct(a.accuracy), pct(a.precision), pct(a.recall), a.status])) :
      `<p><strong>Not yet measured.</strong> The plan is to hand-label a held-out set of protocols on every practice, without looking at the extractor's output, and report accuracy per practice here. Practices below about 90% will be fixed, flagged, or dropped. Until then, every number on this page is unvalidated.</p>`;
    $("#methods-box").innerHTML = `
      <h3>Sample</h3>
      <p><strong>${ALL.length} protocol versions from ${new Set(ALL.map(p => p.parent || p.id)).size} protocol families</strong>${pilot ? ", and the results are provisional until accuracy is measured" : ""}. The target sample is influential, original (non-fork) Ethereum DeFi protocols. Every Ethereum protocol in DefiLlama's snapshot of ${esc(D.snapshot_date)} was ranked by Ethereum TVL; exchanges, bridges, chains, forks and deprecated entries were dropped by rule; the rest were reviewed by hand in TVL order until ${ALL.length} were included. A candidate was skipped when it has no public contracts repository, is custodial, shares a codebase with a protocol already included, or its repository could not be identified with confidence. Every skip is logged with its reason in <code>data/review.csv</code>. Each subject is one version of a codebase (Aave V3, Aave V4), possibly spanning several repositories.</p>
      ${ALL.some(p => p.repo_check) ? (() => {
        const n = k => ALL.filter(p => p.repo_check && p.repo_check.status === k).length;
        return `<p>To check that each repository is the protocol's real code, the contracts DefiLlama reads the protocol's TVL from were looked up on Etherscan and matched against the contracts the repository declares. <strong>${n("auto")} of ${ALL.length}</strong> repositories were confirmed this way${n("hand") ? `, and ${n("hand")} more by hand` : ""}. ${n("unconfirmed") ? `${n("unconfirmed")} are not yet confirmed: they have no distinctive deployed contract to compare against (DefiLlama lists only tokens or an older version, or fetches the contracts from an API) and await a check by hand. ` : ""}${n("doubtful") ? `${n("doubtful")} were flagged as doubtful on review. ` : ""}The explorer marks each protocol: <span class="rc auto" aria-hidden="true">✓</span> confirmed, <span class="rc unconfirmed" aria-hidden="true">?</span> not yet.</p>`;
      })() : ""}
      <p>Charts count <strong>one subject per protocol family</strong> by default: the family's latest version, by launch year. Versions of one team share its habits, so counting every version would weight families with many versions. The switch at the top counts every version instead. Bars in the adoption ranking carry a 95% Wilson interval: with a sample this size, shares that differ by a few points are not distinguishable.</p>
      <h3>What is measured</h3>
      <p><strong>Hover any practice name (the dotted underline) to see exactly what the extractor looks for.</strong> The same text is in <code>out/variables.csv</code> as <code>how_measured</code>.</p>
      <p>${D.variables.filter(v => !v.derived).length} practices, read from a shallow clone of each repository at the scanned commit. No git history and no on-chain data. Tool practices use four ordered levels:</p>
      <ul>
        <li><strong>Run in CI</strong>: a workflow triggered by pushes or pull requests invokes it, directly or through a make / yarn / just target, and a failure can fail the build.</li>
        <li><strong>Configured</strong>: a config file, script entry, or the practice's own code exists; or it runs only on a schedule, by hand, or as an advisory step whose failure is ignored.</li>
        <li><strong>Mentioned</strong>: appears only in docs or comments.</li>
        <li><strong>Not found</strong>: no trace in the scanned repositories.</li>
      </ul>
      <p>Where several repositories belong to one protocol, the strongest evidence wins for practices that can live anywhere (audits, governance, deployment); testing and build practices are read from the core contracts repository.</p>
      <h3>The practice ladder</h3>
      <p>A set of practices forms a ladder when a protocol that has a rarer one also has the more common ones. Loevinger's H measures this: for every pair of practices it counts the protocols that have the rarer one without the more common one, and divides by the count expected if the two were adopted independently. H = 1 − observed / expected, so 0 means no order and 1 a perfect ladder; 0.3, 0.4 and 0.5 are the conventional marks for a weak, medium and strong scale. A practice counts as found when it is configured or better. The five rungs were chosen after looking at the data, so this is an exploratory result, and part of the order is technical rather than a choice: fuzz and invariant tests are tests, and Foundry invariant tests run on the fuzzer.</p>
      <h3>Not applicable, unknown, error</h3>
      <p><strong>Not applicable</strong> cells (for example upgrade checks on an immutable protocol) are excluded from adoption rates. <strong>Unknown</strong> means the relevant part of the protocol lives outside the scanned repositories. <strong>Error</strong> means a probe failed and its result is not counted as “absent”.</p>
      <h3>Accuracy</h3>
      ${accHtml}
      <h3>Caveats</h3>
      <ul>
        <li><strong>Publicly visible only.</strong> A practice that is not found may still be used in a private repository, a hosted service, or a process that leaves no file behind. “Not found” is not “not done”, and post-launch practices such as monitoring are the least visible.</li>
        <li>“Run in CI” cannot show that a check is <em>required</em> to merge; that is server-side branch protection. Only <code>run:</code> and <code>uses:</code> lines count as invocations, never env vars or artifact paths. Workflow path filters are not evaluated. Where a practice is identified from a workflow's file name rather than a command, or from a reusable workflow hosted in another repository, the evidence says so.</li>
        <li>The scan shows the state at one commit. It says what a repository uses now, not when it started.</li>
        <li>Context labels (upgradeability, admin role, launch year) are entered by hand${P.some(p => p.label_status === "draft") ? "; some are still drafts awaiting review" : ""}.</li>
      </ul>`;
  }

  // ---- theme ------------------------------------------------------------
  (function theme() {
    const root = document.documentElement;
    try { const t = localStorage.getItem("theme"); if (t) root.dataset.theme = t; } catch (e) { /* storage unavailable */ }
    $("#theme").addEventListener("click", () => {
      const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      try { localStorage.setItem("theme", root.dataset.theme); } catch (e) { /* ignore */ }
    });
  })();

  function render() {
    applySample();
    header(); ranking(); saidVsRun(); ladder(); stages(); upgradeability(); toolchain(); renderHeat(); methods();
  }
  document.querySelectorAll("#sample-seg button").forEach(b => b.addEventListener("click", () => {
    sample = b.dataset.sample; render();
  }));
  applySample(); heatmap(); render();

  // deep link: index.html#p=aave-v3&d=D6_upgrade opens that protocol / dimension in the explorer
  const q = new URLSearchParams(location.hash.slice(1));
  if (q.get("p") && P.some(p => p.id === q.get("p"))) {
    const b = document.querySelector(`.cell[data-p="${q.get("p")}"][data-d="${q.get("d")}"]`);
    if (b) b.setAttribute("aria-expanded", "true");
    detail(q.get("p"), q.get("d") || null);
  }
})();
