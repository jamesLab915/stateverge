/**
 * Rule-based parent_event_id linking for country-layer events (same country_code, time window).
 */

function normType(t) {
  return String(t || "")
    .trim()
    .toLowerCase();
}

function linkRule(parent, child) {
  if (String(parent.country_code) !== String(child.country_code)) return false;
  const days =
    (new Date(child.event_date).getTime() - new Date(parent.event_date).getTime()) /
    (86400 * 1000);
  if (days < 0 || days > 120) return false;

  const ta = normType(parent.event_type);
  const tb = normType(child.event_type);

  if (ta === "election" && tb === "policy_shift") return true;
  if (ta === "sanction" && tb === "economic_response") return true;
  if (ta === "conflict" && tb === "escalation") return true;
  if (ta === "reform" && tb === "economic_change") return true;
  return false;
}

/**
 * @param {import("pg").Pool} pool
 */
async function linkCountryEventChains(pool) {
  const { rows } = await pool.query(`
    SELECT id, country_code, event_type, event_date, parent_event_id
    FROM events
    ORDER BY country_code ASC, event_date ASC, id ASC
  `);

  let n = 0;
  for (let i = 0; i < rows.length; i++) {
    const child = rows[i];
    if (child.parent_event_id) continue;
    for (let j = i - 1; j >= 0; j--) {
      const parent = rows[j];
      if (String(parent.country_code) !== String(child.country_code)) break;
      if (!linkRule(parent, child)) continue;
      const r = await pool.query(
        `UPDATE events SET parent_event_id = $1 WHERE id = $2 AND parent_event_id IS NULL`,
        [parent.id, child.id]
      );
      if (r.rowCount) n++;
      break;
    }
  }
  if (n) console.log(`Country event chains linked: ${n} edge(s).`);
}

module.exports = { linkCountryEventChains, linkRule };
