/**
 * Narrative Graph v1 — rule layer only (PostgreSQL-backed).
 * @param {import("pg").Pool} pool
 */

const { classifySeries, computeCountrySnapshotTrends } = require("./trendEngine.js");

const REL = {
  CAUSED: "caused",
  INFLUENCED: "influenced",
  ESCALATED: "escalated",
  STABILIZED: "stabilized",
  INITIATED: "initiated",
  OPPOSED: "opposed",
  SHIFTED: "shifted",
  IMPROVED_INNOVATION: "improved_innovation",
  INCREASED_RISK: "increased_risk",
  REDUCED_OPENNESS: "reduced_openness",
  STRENGTHENED_POWER: "strengthened_power",
  WEAKENED_POWER: "weakened_power",
};

function clamp(n, lo = 0, hi = 100) {
  return Math.max(lo, Math.min(hi, Math.round(Number(n) || 0)));
}

function mapRoleToRelation(role) {
  const r = String(role || "").toLowerCase();
  if (["initiated", "signed", "nominated", "implemented", "proposed"].some((x) => r.includes(x)))
    return REL.INITIATED;
  if (["opposed", "criticized", "blocked", "harmed"].some((x) => r.includes(x))) return REL.OPPOSED;
  if (r.includes("escalat")) return REL.ESCALATED;
  return REL.INFLUENCED;
}

function mapDimensionToRelation(dimension, value) {
  const d = String(dimension || "").toLowerCase();
  const v = Number(value) || 0;
  const pos = v >= 0;
  if (d === "innovation") return pos ? REL.IMPROVED_INNOVATION : REL.SHIFTED;
  if (d === "risk") return pos ? REL.INCREASED_RISK : REL.STABILIZED;
  if (d === "openness") return pos ? REL.STRENGTHENED_POWER : REL.REDUCED_OPENNESS;
  if (d === "governance" || d === "social_order") return pos ? REL.STABILIZED : REL.SHIFTED;
  if (d === "economy" || d === "power_score") return pos ? REL.STRENGTHENED_POWER : REL.WEAKENED_POWER;
  if (d === "opportunity") return pos ? REL.STABILIZED : REL.INCREASED_RISK;
  return REL.INFLUENCED;
}

function dedupeNodes(nodes) {
  const m = new Map();
  for (const n of nodes) {
    if (!m.has(n.id)) m.set(n.id, n);
  }
  return [...m.values()];
}

function dedupeEdges(edges) {
  const s = new Set();
  const out = [];
  for (const e of edges) {
    const k = `${e.from}|${e.to}|${e.relation}`;
    if (s.has(k)) continue;
    s.add(k);
    out.push(e);
  }
  return out;
}

function actorGlobalId(actorId) {
  return `actor:global:${actorId}`;
}

function eventNationalId(id) {
  return `event:${id}`;
}

function federalEventId(id) {
  return `federal_event:${id}`;
}

function federalActorId(slug) {
  return `actor:federal:${slug}`;
}

function countryId(code) {
  return `country:${code}`;
}

/** Every consecutive pair in path must exist as a directed edge (calibration: traceable key paths). */
function keyPathSupported(path, edges) {
  const pairs = new Set(edges.map((e) => `${e.from}|${e.to}`));
  for (let i = 0; i < path.length - 1; i++) {
    if (!pairs.has(`${path[i]}|${path[i + 1]}`)) return false;
  }
  return true;
}

function filterKeyPaths(paths, edges) {
  return paths.filter((kp) => keyPathSupported(kp.path, edges));
}

async function loadCountryTrendBundle(pool, countryCode) {
  const N = 5;
  const snapRes = await pool.query(
    `
    SELECT id, snapshot_date, overall, governance, economy, innovation, openness,
           infrastructure, future_potential, human_capital, social_order,
           risk, opportunity, power_score
    FROM country_score_snapshots
    WHERE country_code = $1
    ORDER BY id DESC
    LIMIT $2
    `,
    [countryCode, N]
  );
  const chron = [...snapRes.rows].reverse();
  const overallSeries = chron.map((r) => Number(r.overall ?? 0));
  const powerSeries = chron.map((r) =>
    r.power_score != null && Number.isFinite(Number(r.power_score))
      ? Number(r.power_score)
      : Number(r.overall ?? 0)
  );
  const riskSeries = chron.map((r) => Number(r.risk ?? 0));
  const opportunitySeries = chron.map((r) => Number(r.opportunity ?? 0));
  const oCls = classifySeries(overallSeries, "overall");
  let trendBundle = null;
  if (chron.length >= 2) {
    trendBundle = computeCountrySnapshotTrends({
      overallSeries,
      powerSeries,
      riskSeries,
      opportunitySeries,
    });
  }
  return { chron, oCls, trendBundle };
}

/**
 * @param {import("pg").Pool} pool
 * @param {string} countryCode
 */
async function buildCountryNarrativeGraph(pool, countryCode) {
  const nodes = [];
  const edges = [];

  const cRes = await pool.query(
    `
    SELECT c.code, c.name, s.overall, s.power_score, s.risk, s.opportunity, s.innovation, s.governance
    FROM countries c
    JOIN country_scores s ON s.country_code = c.code
    WHERE c.code = $1
    `,
    [countryCode]
  );
  const row = cRes.rows[0];
  if (!row) {
    return {
      nodes: [],
      edges: [],
      summary: "Country not found in ledger.",
      key_paths: [],
    };
  }

  const { chron, oCls, trendBundle } = await loadCountryTrendBundle(pool, countryCode);
  const cImportance = clamp(
    Number(row.overall ?? 0) * 0.5 + Number(row.power_score ?? 0) * 0.5
  );
  nodes.push({
    id: countryId(countryCode),
    type: "country",
    label: row.name,
    importance: cImportance,
  });

  const evRes = await pool.query(
    `
    SELECT id, title, impact_strength, event_type, parent_event_id, event_date
    FROM events
    WHERE country_code = $1
    ORDER BY event_date DESC NULLS LAST, id DESC
    LIMIT 15
    `,
    [countryCode]
  );
  const events = evRes.rows;
  const eventIds = events.map((e) => e.id);

  let consRows = { rows: [] };
  if (eventIds.length) {
    consRows = await pool.query(
      `
      SELECT event_id, dimension, impact_value
      FROM event_consequences
      WHERE target_country_code = $1 AND event_id = ANY($2::int[])
      `,
      [countryCode, eventIds]
    );
  }
  const consByEvent = new Map();
  for (const r of consRows.rows) {
    const list = consByEvent.get(r.event_id) || [];
    list.push(r);
    consByEvent.set(r.event_id, list);
  }

  let actRows = { rows: [] };
  if (eventIds.length) {
    actRows = await pool.query(
      `
      SELECT ea.event_id, ea.actor_id, ea.role, a.name, a.actor_type
      FROM event_actors ea
      JOIN actors a ON a.id = ea.actor_id
      WHERE ea.event_id = ANY($1::int[])
      `,
      [eventIds]
    );
  }
  const actsByEvent = new Map();
  for (const r of actRows.rows) {
    const list = actsByEvent.get(r.event_id) || [];
    list.push(r);
    actsByEvent.set(r.event_id, list);
  }

  const seenEventIds = new Set(events.map((e) => e.id));
  const parentIds = [
    ...new Set(
      events
        .map((e) => e.parent_event_id)
        .filter((pid) => pid != null && !seenEventIds.has(pid))
    ),
  ];
  let parentTitles = new Map();
  if (parentIds.length) {
    const pRes = await pool.query(
      `SELECT id, title FROM events WHERE id = ANY($1::int[])`,
      [parentIds]
    );
    parentTitles = new Map(pRes.rows.map((r) => [r.id, r.title]));
  }

  for (const ev of events) {
    const cons = consByEvent.get(ev.id) || [];
    const mag = cons.reduce((a, r) => a + Math.abs(Number(r.impact_value) || 0), 0);
    const imp = clamp(
      Number(ev.impact_strength || 0) * 4 + Math.min(40, mag * 2)
    );
    nodes.push({
      id: eventNationalId(ev.id),
      type: "event",
      label: ev.title,
      importance: imp,
    });
    const rel =
      cons.length > 0
        ? mapDimensionToRelation(cons[0].dimension, cons[0].impact_value)
        : REL.INFLUENCED;
    edges.push({
      from: eventNationalId(ev.id),
      to: countryId(countryCode),
      relation: rel,
    });
    if (ev.parent_event_id) {
      if (!seenEventIds.has(ev.parent_event_id)) {
        seenEventIds.add(ev.parent_event_id);
        nodes.push({
          id: eventNationalId(ev.parent_event_id),
          type: "event",
          label:
            parentTitles.get(ev.parent_event_id) ||
            `Prior event #${ev.parent_event_id}`,
          importance: clamp(imp - 5),
        });
      }
      edges.push({
        from: eventNationalId(ev.parent_event_id),
        to: eventNationalId(ev.id),
        relation: REL.CAUSED,
      });
    }
  }

  const seenActor = new Set();
  for (const r of actRows.rows) {
    const aid = actorGlobalId(r.actor_id);
    if (!seenActor.has(aid)) {
      seenActor.add(aid);
      const base = 48 + Math.min(25, String(r.actor_type || "").length * 2);
      nodes.push({
        id: aid,
        type: "actor",
        label: r.name,
        importance: clamp(base),
      });
    }
    edges.push({
      from: aid,
      to: eventNationalId(r.event_id),
      relation: mapRoleToRelation(r.role),
    });
  }

  if (countryCode === "us") {
    const feRes = await pool.query(
      `
      SELECT id, title, impact_strength, event_date
      FROM federal_events
      ORDER BY event_date DESC, id DESC
      LIMIT 12
      `
    );
    const feIds = feRes.rows.map((r) => r.id);
    let fecRows = { rows: [] };
    let feaRows = { rows: [] };
    if (feIds.length) {
      fecRows = await pool.query(
        `
        SELECT event_id, dimension, impact_value
        FROM federal_event_consequences
        WHERE event_id = ANY($1::int[])
        `,
        [feIds]
      );
      feaRows = await pool.query(
        `
        SELECT fea.event_id, fea.role, fa.slug, fa.name,
               fa.formal_power_score, fa.political_influence_score
        FROM federal_event_actors fea
        JOIN federal_actors fa ON fa.id = fea.actor_id
        WHERE fea.event_id = ANY($1::int[])
        `,
        [feIds]
      );
    }
    const fecByFe = new Map();
    for (const r of fecRows.rows) {
      const list = fecByFe.get(r.event_id) || [];
      list.push(r);
      fecByFe.set(r.event_id, list);
    }

    for (const fe of feRes.rows) {
      const eid = federalEventId(fe.id);
      const fec = fecByFe.get(fe.id) || [];
      const imp = clamp(
        Number(fe.impact_strength || 4) * 8 +
          Math.min(20, fec.reduce((a, x) => a + Math.abs(Number(x.impact_value) || 0), 0))
      );
      nodes.push({
        id: eid,
        type: "event",
        label: fe.title,
        importance: imp,
      });
      const rel =
        fec.length > 0
          ? mapDimensionToRelation(fec[0].dimension, fec[0].impact_value)
          : REL.INFLUENCED;
      edges.push({
        from: eid,
        to: countryId("us"),
        relation: rel,
      });
    }

    const fedActorSeen = new Set();
    for (const r of feaRows.rows) {
      const aid = federalActorId(r.slug);
      if (!fedActorSeen.has(aid)) {
        fedActorSeen.add(aid);
        nodes.push({
          id: aid,
          type: "actor",
          label: r.name,
          importance: clamp(
            Number(r.formal_power_score ?? 50) * 0.55 +
              Number(r.political_influence_score ?? 50) * 0.45
          ),
        });
      }
      edges.push({
        from: aid,
        to: federalEventId(r.event_id),
        relation: mapRoleToRelation(r.role),
      });
    }
  }

  const finalNodes = dedupeNodes(nodes);
  const finalEdges = dedupeEdges(edges);

  const scoredEvents = events
    .map((ev) => {
      const cons = consByEvent.get(ev.id) || [];
      const mag = cons.reduce((a, r) => a + Math.abs(Number(r.impact_value) || 0), 0);
      return { ev, mag, cons };
    })
    .sort((a, b) => b.mag - a.mag);

  const key_paths = [];
  const cid = countryId(countryCode);
  for (const { ev, cons } of scoredEvents.slice(0, 6)) {
    const acts = actsByEvent.get(ev.id) || [];
    if (acts.length && key_paths.length < 5) {
      const a = acts[0];
      key_paths.push({
        path: [actorGlobalId(a.actor_id), eventNationalId(ev.id), cid],
        why_it_matters: `Actor "${a.name}" (${a.role}) links to "${ev.title}"; consequences on ${row.name} span ${cons.length} ledger dimension(s).`,
      });
    }
  }
  for (const { ev, cons } of scoredEvents.slice(0, 6)) {
    if (key_paths.length >= 5) break;
    const acts = actsByEvent.get(ev.id) || [];
    if (acts.length) continue;
    if (ev.parent_event_id && key_paths.length < 5) {
      key_paths.push({
        path: [
          eventNationalId(ev.parent_event_id),
          eventNationalId(ev.id),
          cid,
        ],
        why_it_matters: `Event chain: parent #${ev.parent_event_id} → "${ev.title}" feeds the national ledger (${cons.length} consequence row(s)).`,
      });
    } else if (key_paths.length < 5) {
      key_paths.push({
        path: [eventNationalId(ev.id), cid],
        why_it_matters: `Recorded event "${ev.title}" projects into ${row.name} scores via event_consequences.`,
      });
    }
  }

  if (countryCode === "us") {
    const feList = await pool.query(
      `
      SELECT fe.id, fe.title
      FROM federal_events fe
      ORDER BY fe.event_date DESC, fe.id DESC
      LIMIT 5
      `
    );
    const feIdsSmall = feList.rows.map((r) => r.id);
    if (feIdsSmall.length) {
      const feaQ = await pool.query(
        `
        SELECT fea.event_id, fea.role, fa.slug, fa.name
        FROM federal_event_actors fea
        JOIN federal_actors fa ON fa.id = fea.actor_id
        WHERE fea.event_id = ANY($1::int[])
        `,
        [feIdsSmall]
      );
      const byFe = new Map();
      for (const r of feaQ.rows) {
        const list = byFe.get(r.event_id) || [];
        list.push(r);
        byFe.set(r.event_id, list);
      }
      for (const fe of feList.rows) {
        if (key_paths.length >= 5) break;
        const lst = byFe.get(fe.id) || [];
        if (!lst.length) continue;
        const a = lst[0];
        key_paths.push({
          path: [federalActorId(a.slug), federalEventId(fe.id), cid],
          why_it_matters: `Federal path: ${a.name} (${a.role}) → "${fe.title}" → US national ledger.`,
        });
      }
    }
  }

  while (key_paths.length < 3 && scoredEvents.length) {
    const { ev } = scoredEvents[Math.min(key_paths.length, scoredEvents.length - 1)];
    key_paths.push({
      path: [eventNationalId(ev.id), cid],
      why_it_matters: "National event → country score channel (rule path).",
    });
    if (key_paths.length >= 3) break;
  }

  const trendLine = trendBundle
    ? `${oCls.label} · ${trendBundle.overall_trend_extended.split(" — ")[0] || ""}`
    : oCls.label;
  const summary = `${row.name}: ${finalNodes.length} nodes / ${finalEdges.length} edges. Snapshot window trend: ${trendLine}. Ledger overall≈${Math.round(Number(row.overall ?? 0))}, risk≈${Math.round(Number(row.risk ?? 0))}.`;

  const traced = filterKeyPaths(key_paths, finalEdges);

  return {
    nodes: finalNodes,
    edges: finalEdges,
    summary,
    key_paths: traced.slice(0, 5),
  };
}

/**
 * @param {import("pg").Pool} pool
 * @param {string} leftCode
 * @param {string} rightCode
 */
async function buildCompareNarrativeGraph(pool, leftCode, rightCode) {
  const [gL, gR] = await Promise.all([
    buildCountryNarrativeGraph(pool, leftCode),
    buildCountryNarrativeGraph(pool, rightCode),
  ]);

  const nodeMap = new Map();
  for (const n of [...gL.nodes, ...gR.nodes]) {
    if (!nodeMap.has(n.id)) nodeMap.set(n.id, n);
  }
  const nodes = [...nodeMap.values()];

  const edgeSet = new Set();
  const edges = [];
  function addEdge(e) {
    const k = `${e.from}|${e.to}|${e.relation}`;
    if (edgeSet.has(k)) return;
    edgeSet.add(k);
    edges.push(e);
  }
  for (const e of [...gL.edges, ...gR.edges]) addEdge(e);
  addEdge({
    from: countryId(leftCode),
    to: countryId(rightCode),
    relation: REL.INFLUENCED,
  });
  addEdge({
    from: countryId(rightCode),
    to: countryId(leftCode),
    relation: REL.INFLUENCED,
  });

  const [ctxL, ctxR] = await Promise.all([
    loadCountryTrendBundle(pool, leftCode),
    loadCountryTrendBundle(pool, rightCode),
  ]);
  const lRow = await pool.query(
    `SELECT c.name, s.risk, s.overall FROM countries c JOIN country_scores s ON s.country_code = c.code WHERE c.code = $1`,
    [leftCode]
  );
  const rRow = await pool.query(
    `SELECT c.name, s.risk, s.overall FROM countries c JOIN country_scores s ON s.country_code = c.code WHERE c.code = $1`,
    [rightCode]
  );
  const ln = lRow.rows[0]?.name || leftCode;
  const rn = rRow.rows[0]?.name || rightCode;
  const riskL = Number(lRow.rows[0]?.risk ?? 0);
  const riskR = Number(rRow.rows[0]?.risk ?? 0);
  const evCountL = gL.nodes.filter((n) => n.type === "event").length;
  const evCountR = gR.nodes.filter((n) => n.type === "event").length;
  const stabName = riskL <= riskR ? ln : rn;
  const shockName = evCountL >= evCountR ? rn : ln;

  const divergence_summary = `${ln} overall trend reads ${ctxL.oCls.label}; ${rn} reads ${ctxR.oCls.label}. Risk ledger: ${riskL} vs ${riskR} (${stabName} lower risk in this window). Event-surface: ${evCountL} vs ${evCountR} narrative events (${shockName} shows fewer listed events in this merge). Paired contrast wording is intentionally omitted here to avoid duplicating the Evolution Contrast block.`.trim();

  const left_arc = `${ln}: ${gL.summary}`;
  const right_arc = `${rn}: ${gR.summary}`;

  const key_paths = [];
  const le = gL.nodes.filter((n) => n.type === "event").sort((a, b) => b.importance - a.importance);
  const re = gR.nodes.filter((n) => n.type === "event").sort((a, b) => b.importance - a.importance);
  if (le[0]) {
    key_paths.push({
      path: [le[0].id, countryId(leftCode), countryId(rightCode)],
      why_it_matters: `High-weight ledger event on the left (${le[0].label}), then cross-country bridge (associational; not causal).`,
    });
  }
  if (re[0] && key_paths.length < 6) {
    key_paths.push({
      path: [re[0].id, countryId(rightCode), countryId(leftCode)],
      why_it_matters: `Right-side ledger event (${re[0].label}) paired with cross-country bridge (associational).`,
    });
  }
  for (const p of gL.key_paths.slice(0, 2)) key_paths.push(p);
  for (const p of gR.key_paths.slice(0, 2)) key_paths.push(p);

  const involvesUs = leftCode === "us" || rightCode === "us";
  if (involvesUs) {
    const usCode = leftCode === "us" ? leftCode : rightCode;
    const other = leftCode === "us" ? rightCode : leftCode;
    const fea = await pool.query(
      `
      SELECT fa.slug, fa.name, fe.id AS event_id, fe.title
      FROM federal_event_actors fea
      JOIN federal_actors fa ON fa.id = fea.actor_id
      JOIN federal_events fe ON fe.id = fea.event_id
      ORDER BY fe.event_date DESC, fe.id DESC
      LIMIT 1
      `
    );
    if (fea.rows[0] && key_paths.length < 8) {
      const fr = fea.rows[0];
      key_paths.push({
        path: [
          federalActorId(fr.slug),
          federalEventId(fr.event_id),
          countryId(usCode),
          countryId(other),
        ],
        why_it_matters: `US federal actor linkage on "${fr.title}" bridges to the paired country node (directed edges only).`,
      });
    }
  }

  const tracedCompare = filterKeyPaths(key_paths, edges);

  return {
    nodes,
    edges,
    summary: `Compare narrative graph: ${nodes.length} nodes, ${edges.length} edges. ${divergence_summary.slice(0, 400)}`,
    key_paths: tracedCompare.slice(0, 6),
    divergence_summary,
    left_arc,
    right_arc,
  };
}

module.exports = {
  buildCountryNarrativeGraph,
  buildCompareNarrativeGraph,
  keyPathSupported,
  filterKeyPaths,
};
