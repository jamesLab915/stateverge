const { createPgPool } = require("../lib/db/pgPool.js");

const pool = createPgPool();

async function ensurePosition(
  client,
  { office_title, branch, department, formal_power_weight, is_senate_confirmed_role, order_rank, summary }
) {
  const { rows } = await client.query(
    `
    SELECT id FROM federal_positions
    WHERE office_title = $1 AND branch = $2
      AND COALESCE(department, '') = COALESCE($3, '')
    `,
    [office_title, branch, department]
  );
  if (rows[0]) return rows[0].id;
  const ins = await client.query(
    `
    INSERT INTO federal_positions (
      office_title, branch, department, formal_power_weight,
      is_senate_confirmed_role, order_rank, summary
    ) VALUES ($1,$2,$3,$4,$5,$6,$7)
    RETURNING id
    `,
    [
      office_title,
      branch,
      department,
      formal_power_weight,
      is_senate_confirmed_role,
      order_rank,
      summary,
    ]
  );
  return ins.rows[0].id;
}

async function ensureActor(client, row) {
  const { rows } = await client.query(`SELECT id FROM federal_actors WHERE slug = $1`, [
    row.slug,
  ]);
  if (rows[0]) return rows[0].id;
  const ins = await client.query(
    `
    INSERT INTO federal_actors (
      slug, name, actor_type, office_title, branch, department, country_code,
      is_active, summary, formal_power_score, political_influence_score,
      conflict_index, agenda_alignment_score, x_signal_score, power_status
    ) VALUES ($1,$2,$3,$4,$5,$6,'us',TRUE,$7,$8,$9,$10,$11,NULL,$12)
    RETURNING id
    `,
    [
      row.slug,
      row.name,
      row.actor_type,
      row.office_title,
      row.branch,
      row.department,
      row.summary,
      row.formal_power_score,
      row.political_influence_score,
      row.conflict_index,
      row.agenda_alignment_score,
      row.power_status,
    ]
  );
  return ins.rows[0].id;
}

async function main() {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");

    const pPotus = await ensurePosition(client, {
      office_title: "President of the United States",
      branch: "executive",
      department: "Executive Office of the President",
      formal_power_weight: 100,
      is_senate_confirmed_role: false,
      order_rank: 1,
      summary: "Head of state and government; formal power per Constitution and statute.",
    });
    const pVp = await ensurePosition(client, {
      office_title: "Vice President",
      branch: "executive",
      department: "Executive Office of the President",
      formal_power_weight: 92,
      is_senate_confirmed_role: false,
      order_rank: 2,
      summary: "President of the Senate; succeeds President.",
    });
    const pSecState = await ensurePosition(client, {
      office_title: "Secretary of State",
      branch: "executive",
      department: "Department of State",
      formal_power_weight: 88,
      is_senate_confirmed_role: true,
      order_rank: 10,
      summary: "Chief diplomat; Senate-confirmed cabinet.",
    });
    const pSecDef = await ensurePosition(client, {
      office_title: "Secretary of Defense",
      branch: "executive",
      department: "Department of Defense",
      formal_power_weight: 88,
      is_senate_confirmed_role: true,
      order_rank: 11,
      summary: "Civilian control of military; major resource authority.",
    });
    const pCos = await ensurePosition(client, {
      office_title: "White House Chief of Staff",
      branch: "executive",
      department: "The White House",
      formal_power_weight: 78,
      is_senate_confirmed_role: false,
      order_rank: 5,
      summary: "Gatekeeper to President; high informal leverage.",
    });
    const pMaj = await ensurePosition(client, {
      office_title: "Senate Majority Leader",
      branch: "legislative",
      department: "United States Senate",
      formal_power_weight: 72,
      is_senate_confirmed_role: false,
      order_rank: 30,
      summary: "Controls Senate floor agenda.",
    });

    const aPotus = await ensureActor(client, {
      slug: "us-president",
      name: "President (incumbent — update in operations DB)",
      actor_type: "president",
      office_title: "President of the United States",
      branch: "executive",
      department: "Executive Office of the President",
      summary:
        "Demo row: replace name with current officeholder; formal power derives from position weight, not AI.",
      formal_power_score: 100,
      political_influence_score: 98,
      conflict_index: 12,
      agenda_alignment_score: 55,
      power_status: "Central Actor",
    });
    const aVp = await ensureActor(client, {
      slug: "us-vice-president",
      name: "Vice President (incumbent — update)",
      actor_type: "vice_president",
      office_title: "Vice President",
      branch: "executive",
      department: "Executive Office of the President",
      summary: "Demo row for VP office.",
      formal_power_score: 92,
      political_influence_score: 78,
      conflict_index: 8,
      agenda_alignment_score: 52,
      power_status: "Stable Actor",
    });
    const aState = await ensureActor(client, {
      slug: "us-secretary-state",
      name: "Secretary of State (incumbent — update)",
      actor_type: "cabinet",
      office_title: "Secretary of State",
      branch: "executive",
      department: "Department of State",
      summary: "Cabinet principal for foreign policy.",
      formal_power_score: 88,
      political_influence_score: 82,
      conflict_index: 22,
      agenda_alignment_score: 48,
      power_status: "Stable Actor",
    });
    const aDef = await ensureActor(client, {
      slug: "us-secretary-defense",
      name: "Secretary of Defense (incumbent — update)",
      actor_type: "cabinet",
      office_title: "Secretary of Defense",
      branch: "executive",
      department: "Department of Defense",
      summary: "Cabinet control of military bureaucracy.",
      formal_power_score: 88,
      political_influence_score: 80,
      conflict_index: 18,
      agenda_alignment_score: 50,
      power_status: "Stable Actor",
    });
    const aCos = await ensureActor(client, {
      slug: "us-chief-of-staff",
      name: "White House Chief of Staff (incumbent — update)",
      actor_type: "advisor",
      office_title: "White House Chief of Staff",
      branch: "executive",
      department: "The White House",
      summary: "Staff power center; not Senate-confirmed.",
      formal_power_score: 78,
      political_influence_score: 85,
      conflict_index: 28,
      agenda_alignment_score: 58,
      power_status: "Rising Actor",
    });
    const aMaj = await ensureActor(client, {
      slug: "us-senate-majority-leader",
      name: "Senate Majority Leader (incumbent — update)",
      actor_type: "senator",
      office_title: "Senate Majority Leader",
      branch: "legislative",
      department: "United States Senate",
      summary: "Legislative gatekeeping role.",
      formal_power_score: 72,
      political_influence_score: 76,
      conflict_index: 35,
      agenda_alignment_score: 45,
      power_status: "Contested Actor",
    });

    async function ensureHistory(actorId, positionId, start, status, conf, src) {
      const { rows } = await client.query(
        `
        SELECT id FROM actor_position_history
        WHERE actor_id = $1 AND position_id = $2 AND end_date IS NULL
        `,
        [actorId, positionId]
      );
      if (rows[0]) return;
      await client.query(
        `
        INSERT INTO actor_position_history (
          actor_id, position_id, start_date, end_date, status,
          source_type, source_name, source_url, confidence
        ) VALUES ($1,$2,$3::date,NULL,$4,$5,$6,$7,$8)
        `,
        [
          actorId,
          positionId,
          start,
          status,
          src.type,
          src.name,
          src.url,
          conf,
        ]
      );
    }

    const official = { type: "official", name: "White House / public schedule", url: null };
    const media = {
      type: "reputable_media",
      name: "Press reporting (verify)",
      url: null,
    };

    await ensureHistory(aPotus, pPotus, "2021-01-20", "active", "confirmed", official);
    await ensureHistory(aVp, pVp, "2021-01-20", "active", "confirmed", official);
    await ensureHistory(aState, pSecState, "2021-01-26", "active", "confirmed", {
      type: "congressional",
      name: "Senate record",
      url: null,
    });
    await ensureHistory(aDef, pSecDef, "2021-01-22", "active", "confirmed", {
      type: "congressional",
      name: "Senate record",
      url: null,
    });
    await ensureHistory(aCos, pCos, "2023-02-01", "active", "contested", media);
    await ensureHistory(aMaj, pMaj, "2023-01-03", "active", "confirmed", {
      type: "congressional",
      name: "Senate party organization",
      url: null,
    });

    let e1, e2, e3;
    const ex = await client.query(
      `SELECT id FROM federal_events WHERE title = $1`,
      ["FY defense policy directive issued"]
    );
    if (ex.rows[0]) e1 = ex.rows[0].id;
    else {
      const r = await client.query(
        `
        INSERT INTO federal_events (
          event_type, title, summary, event_date, branch, department,
          impact_direction, impact_strength, source_type, source_name, confidence
        ) VALUES (
          'policy_shift',
          'FY defense policy directive issued',
          'Executive branch policy guidance affecting DOD priorities (illustrative seed).',
          CURRENT_DATE - 12,
          'executive',
          'Department of Defense',
          'neutral',
          6,
          'institutional',
          'DOD public affairs',
          'confirmed'
        ) RETURNING id
        `
      );
      e1 = r.rows[0].id;
    }

    const ex2 = await client.query(`SELECT id FROM federal_events WHERE title = $1`, [
      "Cabinet principal testifies on Hill",
    ]);
    if (ex2.rows[0]) e2 = ex2.rows[0].id;
    else {
      const r = await client.query(
        `
        INSERT INTO federal_events (
          event_type, title, summary, event_date, branch, department,
          impact_direction, impact_strength, source_type, source_name, confidence
        ) VALUES (
          'congressional_action',
          'Cabinet principal testifies on Hill',
          'Oversight hearing; signals legislative–executive friction (illustrative).',
          CURRENT_DATE - 8,
          'legislative',
          'United States Senate',
          'negative',
          5,
          'reputable_media',
          'Major national outlet',
          'contested'
        ) RETURNING id
        `
      );
      e2 = r.rows[0].id;
    }

    const ex3 = await client.query(`SELECT id FROM federal_events WHERE title = $1`, [
      "Staff leadership change at White House",
    ]);
    if (ex3.rows[0]) e3 = ex3.rows[0].id;
    else {
      const r = await client.query(
        `
        INSERT INTO federal_events (
          event_type, title, summary, event_date, branch, department,
          impact_direction, impact_strength, source_type, source_name, confidence
        ) VALUES (
          'appointment',
          'Staff leadership change at White House',
          'Chief of staff turnover narrative (illustrative seed — verify in production).',
          CURRENT_DATE - 21,
          'executive',
          'The White House',
          'negative',
          7,
          'reputable_media',
          'Press pool',
          'speculative'
        ) RETURNING id
        `
      );
      e3 = r.rows[0].id;
    }

    async function linkEA(eid, aid, role, stance) {
      await client.query(
        `
        INSERT INTO federal_event_actors (event_id, actor_id, role, stance)
        VALUES ($1,$2,$3,$4)
        ON CONFLICT (event_id, actor_id, role) DO NOTHING
        `,
        [eid, aid, role, stance]
      );
    }

    await linkEA(e1, aDef, "implemented", "neutral");
    await linkEA(e1, aPotus, "signed", "support");
    await linkEA(e2, aState, "defended", "support");
    await linkEA(e2, aMaj, "criticized", "oppose");
    await linkEA(e3, aCos, "removed", "neutral");
    await linkEA(e3, aPotus, "initiated", "support");

    async function ensureCons(eid, rows) {
      for (const c of rows) {
        const chk = await client.query(
          `SELECT 1 FROM federal_event_consequences WHERE event_id = $1 AND dimension = $2 AND COALESCE(explanation,'') = $3`,
          [eid, c.dimension, c.explanation || ""]
        );
        if (chk.rows.length) continue;
        await client.query(
          `
          INSERT INTO federal_event_consequences (
            event_id, target_type, target_actor_id, target_department,
            dimension, impact_value, time_horizon, confidence, explanation
          ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
          `,
          [
            eid,
            c.target_type,
            c.target_actor_id,
            c.target_department,
            c.dimension,
            c.impact_value,
            c.time_horizon,
            c.confidence,
            c.explanation,
          ]
        );
      }
    }

    await ensureCons(e1, [
      {
        target_type: "department",
        target_actor_id: null,
        target_department: "Department of Defense",
        dimension: "policy_control",
        impact_value: 4,
        time_horizon: "medium_term",
        confidence: "confirmed",
        explanation: "Policy directive shifts implementation emphasis.",
      },
      {
        target_type: "actor",
        target_actor_id: aDef,
        target_department: null,
        dimension: "formal_power",
        impact_value: 1,
        time_horizon: "short_term",
        confidence: "confirmed",
        explanation: "Cabinet execution load increases visibility.",
      },
    ]);

    await ensureCons(e2, [
      {
        target_type: "actor",
        target_actor_id: aState,
        target_department: null,
        dimension: "conflict",
        impact_value: 6,
        time_horizon: "immediate",
        confidence: "contested",
        explanation: "Hearing increases public friction exposure.",
      },
      {
        target_type: "actor",
        target_actor_id: aMaj,
        target_department: null,
        dimension: "political_influence",
        impact_value: 3,
        time_horizon: "short_term",
        confidence: "contested",
        explanation: "Floor narrative competition.",
      },
    ]);

    await ensureCons(e3, [
      {
        target_type: "actor",
        target_actor_id: aCos,
        target_department: null,
        dimension: "stability",
        impact_value: -5,
        time_horizon: "short_term",
        confidence: "speculative",
        explanation: "Staff turnover often precedes process disruption (hedged).",
      },
      {
        target_type: "actor",
        target_actor_id: aPotus,
        target_department: null,
        dimension: "alignment",
        impact_value: 2,
        time_horizon: "immediate",
        confidence: "speculative",
        explanation: "Principal reasserts staffing control (interpretive).",
      },
    ]);

    const sn = await client.query(
      `SELECT id FROM federal_timeline_snapshots WHERE snapshot_date = CURRENT_DATE`
    );
    if (!sn.rows[0]) {
      await client.query(
        `
        INSERT INTO federal_timeline_snapshots (
          snapshot_date, overall_power_stability, executive_cohesion, cabinet_stability,
          legislative_alignment, conflict_temperature, narrative_pressure, note
        ) VALUES (
          CURRENT_DATE,
          68, 62, 70, 44, 58, 52,
          'Seed snapshot: composite indices 0–100; replace with computed pipeline when available.'
        )
        `
      );
    }
    const sn2 = await client.query(
      `SELECT id FROM federal_timeline_snapshots WHERE snapshot_date = CURRENT_DATE - 14`
    );
    if (!sn2.rows[0]) {
      await client.query(
        `
        INSERT INTO federal_timeline_snapshots (
          snapshot_date, overall_power_stability, executive_cohesion, cabinet_stability,
          legislative_alignment, conflict_temperature, narrative_pressure, note
        ) VALUES (
          CURRENT_DATE - 14,
          71, 65, 72, 46, 52, 48,
          'Prior seed snapshot for delta visualization.'
        )
        `
      );
    }

    await client.query("COMMIT");
    console.log("US Federal v1 seed complete (idempotent inserts).");
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  } finally {
    client.release();
    await pool.end();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
