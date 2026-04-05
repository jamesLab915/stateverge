import OpenAI from "openai";

const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

export type NarrativeGraphNode = {
  id: string;
  type: string;
  label: string;
  importance: number;
};

export type NarrativeGraphEdge = {
  from: string;
  to: string;
  relation: string;
};

export type NarrativeKeyPath = {
  path: string[];
  why_it_matters: string;
};

export type CountryNarrativeGraphPayload = {
  nodes: NarrativeGraphNode[];
  edges: NarrativeGraphEdge[];
  summary: string;
  key_paths: NarrativeKeyPath[];
};

export type CompareNarrativeGraphPayload = CountryNarrativeGraphPayload & {
  divergence_summary: string;
  left_arc: string;
  right_arc: string;
};

function stripJson(text: string) {
  const t = text.trim();
  const start = t.indexOf("{");
  const end = t.lastIndexOf("}");
  if (start >= 0 && end > start) return t.slice(start, end + 1);
  return t;
}

/**
 * Polishes wording only. Caller must keep nodes, edges, importance, and path arrays from rules.
 */
export async function polishCountryNarrativeGraphText(
  rule: CountryNarrativeGraphPayload
): Promise<Pick<CountryNarrativeGraphPayload, "summary" | "key_paths">> {
  const pathsIn = rule.key_paths.map((k) => ({
    path: k.path,
    why_it_matters: k.why_it_matters,
  }));
  const prompt = `Editor for a narrative graph text layer (PostgreSQL rule engine is source of truth).

RULE summary (may rephrase for clarity only):
${rule.summary}

RULE key paths (each "path" array is FIXED — copy exactly into output):
${JSON.stringify(pathsIn).slice(0, 14000)}

Return STRICT JSON only:
{
  "summary": "2-4 sentences, clearer wording, no new facts or numbers",
  "key_paths": [ { "path": [...same ids as input in same order...], "why_it_matters": "..." } ]
}

The path arrays MUST match the input paths byte-for-byte in order and content.`;

  const res = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    temperature: 0.3,
  });
  const text = res.choices[0]?.message?.content || "{}";
  try {
    const parsed = JSON.parse(stripJson(text)) as {
      summary?: string;
      key_paths?: NarrativeKeyPath[];
    };
    const summary =
      typeof parsed.summary === "string" ? parsed.summary : rule.summary;
    const key_paths = rule.key_paths.map((kp, i) => {
      const c = parsed.key_paths?.[i];
      const pathOk =
        c &&
        Array.isArray(c.path) &&
        JSON.stringify(c.path) === JSON.stringify(kp.path);
      return {
        path: kp.path,
        why_it_matters:
          pathOk && typeof c.why_it_matters === "string"
            ? c.why_it_matters
            : kp.why_it_matters,
      };
    });
    return { summary, key_paths };
  } catch {
    return { summary: rule.summary, key_paths: rule.key_paths };
  }
}

export async function polishCompareNarrativeGraphText(
  rule: CompareNarrativeGraphPayload
): Promise<
  Pick<
    CompareNarrativeGraphPayload,
    "summary" | "key_paths" | "divergence_summary" | "left_arc" | "right_arc"
  >
> {
  const pathsIn = rule.key_paths.map((k) => ({
    path: k.path,
    why_it_matters: k.why_it_matters,
  }));
  const prompt = `Editor for a two-country narrative graph (rules + DB are truth).

RULE summary:
${rule.summary}

divergence_summary:
${rule.divergence_summary}

left_arc:
${rule.left_arc}

right_arc:
${rule.right_arc}

key paths (path arrays are FIXED — must match output exactly):
${JSON.stringify(pathsIn).slice(0, 14000)}

Return STRICT JSON:
{
  "summary": "short",
  "divergence_summary": "clearer, same facts",
  "left_arc": "clearer",
  "right_arc": "clearer",
  "key_paths": [ { "path": [...same as input...], "why_it_matters": "..." } ]
}`;

  const res = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: prompt }],
    temperature: 0.3,
  });
  const text = res.choices[0]?.message?.content || "{}";
  try {
    const parsed = JSON.parse(stripJson(text)) as {
      summary?: string;
      divergence_summary?: string;
      left_arc?: string;
      right_arc?: string;
      key_paths?: NarrativeKeyPath[];
    };
    const key_paths = rule.key_paths.map((kp, i) => {
      const c = parsed.key_paths?.[i];
      const pathOk =
        c &&
        Array.isArray(c.path) &&
        JSON.stringify(c.path) === JSON.stringify(kp.path);
      return {
        path: kp.path,
        why_it_matters:
          pathOk && typeof c.why_it_matters === "string"
            ? c.why_it_matters
            : kp.why_it_matters,
      };
    });
    return {
      summary: typeof parsed.summary === "string" ? parsed.summary : rule.summary,
      divergence_summary:
        typeof parsed.divergence_summary === "string"
          ? parsed.divergence_summary
          : rule.divergence_summary,
      left_arc: typeof parsed.left_arc === "string" ? parsed.left_arc : rule.left_arc,
      right_arc: typeof parsed.right_arc === "string" ? parsed.right_arc : rule.right_arc,
      key_paths,
    };
  } catch {
    return {
      summary: rule.summary,
      divergence_summary: rule.divergence_summary,
      left_arc: rule.left_arc,
      right_arc: rule.right_arc,
      key_paths: rule.key_paths,
    };
  }
}
