/**
 * Minimal RSS/Atom-ish item extraction (no XML dependency).
 * Sufficient for common public feeds; fragile to malformed markup.
 */

/**
 * Strip CDATA and simple tags for text fields.
 * @param {string} s
 */
function stripMarkup(s) {
  return String(s || "")
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/gi, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * @param {string} block
 * @param {string} tag local name (no namespace)
 */
function firstTagText(block, tag) {
  const re = new RegExp(
    `<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`,
    "i"
  );
  const m = block.match(re);
  return m ? stripMarkup(m[1]) : "";
}

/**
 * @param {string} xml
 * @returns {{ title: string, link: string, pubDate: string, description: string }[]}
 */
function parseRssItems(xml) {
  const items = [];
  const re = /<item\b[^>]*>([\s\S]*?)<\/item>/gi;
  let m;
  while ((m = re.exec(xml)) !== null) {
    const block = m[1];
    const title = firstTagText(block, "title");
    let link = firstTagText(block, "link");
    if (!link) {
      const guid = firstTagText(block, "guid");
      if (guid && /^https?:\/\//i.test(guid)) link = guid;
    }
    const pubDate =
      firstTagText(block, "pubDate") ||
      firstTagText(block, "updated") ||
      firstTagText(block, "dc:date");
    const description =
      firstTagText(block, "description") ||
      firstTagText(block, "content:encoded") ||
      firstTagText(block, "summary") ||
      title;
    items.push({ title, link, pubDate, description });
  }
  return items;
}

module.exports = { parseRssItems, stripMarkup };
