/**
 * 批注接口。
 *
 * 只有 /api/notes* 走这里，其余请求由静态资源直接处理
 * （wrangler.toml 里 run_worker_first 指定）。
 *
 * 鉴权：单个令牌，放在 Worker secret NOTES_TOKEN 里。
 * 带令牌 → 能读写全部（含私有）；不带 → 只能读 public。
 */

const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}

function fail(message, status = 400) {
  return json({ error: message }, status);
}

function authorized(request, env) {
  if (!env.NOTES_TOKEN) return false;
  const header = request.headers.get("authorization") || "";
  const token = header.startsWith("Bearer ") ? header.slice(7).trim() : "";
  if (!token) return false;
  // 定长比较，避免用长度泄露信息
  if (token.length !== env.NOTES_TOKEN.length) return false;
  let diff = 0;
  for (let i = 0; i < token.length; i++) {
    diff |= token.charCodeAt(i) ^ env.NOTES_TOKEN.charCodeAt(i);
  }
  return diff === 0;
}

function rowToNote(row) {
  return {
    id: row.id,
    slug: row.slug,
    block: row.block,
    start: row.start,
    end: row.end,
    quote: row.quote,
    prefix: row.prefix,
    suffix: row.suffix,
    body: row.body,
    visibility: row.visibility,
    status: row.status,
    resolution: row.resolution,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function escapeCell(text) {
  return String(text ?? "").replace(/\|/g, "\\|").replace(/\n+/g, " ").trim();
}

function toMarkdown(slug, notes) {
  const lines = [`# 批注清单 · ${slug}`, ""];
  const open = notes.filter((n) => n.status === "open");
  const rest = notes.filter((n) => n.status !== "open");

  const section = (title, items) => {
    if (!items.length) return;
    lines.push(`## ${title}`, "");
    items.forEach((n, i) => {
      const where = `第 ${n.block} 块 · 偏移 ${n.start}–${n.end}`;
      lines.push(`### ${i + 1}. ${where}`, "");
      lines.push(`> ${n.quote}`, "");
      lines.push(escapeCell(n.body), "");
      if (n.resolution) lines.push(`**处理**：${escapeCell(n.resolution)}`, "");
      lines.push("");
    });
  };

  section("待处理", open);
  section("已处理", rest);
  return lines.join("\n");
}

async function listNotes(url, request, env) {
  const slug = url.searchParams.get("slug");
  const isAuthor = authorized(request, env);

  let sql = "SELECT * FROM notes";
  const args = [];
  const where = [];
  if (slug) {
    where.push("slug = ?");
    args.push(slug);
  }
  if (!isAuthor) where.push("visibility = 'public'");
  if (where.length) sql += " WHERE " + where.join(" AND ");
  sql += " ORDER BY block ASC, start ASC, id ASC";

  const { results } = await env.DB.prepare(sql).bind(...args).all();
  const notes = results.map(rowToNote);

  if (url.searchParams.get("format") === "md") {
    if (!isAuthor) return fail("导出需要令牌", 401);
    return new Response(toMarkdown(slug || "全部", notes), {
      headers: {
        "content-type": "text/markdown; charset=utf-8",
        "cache-control": "no-store",
      },
    });
  }
  return json({ notes, author: isAuthor });
}

async function createNote(request, env) {
  const payload = await request.json().catch(() => null);
  if (!payload) return fail("请求体不是合法 JSON");

  const { slug, block, start, end, quote } = payload;
  const body = (payload.body || "").trim();
  if (!slug || typeof block !== "number" || typeof start !== "number") {
    return fail("缺少 slug / block / start");
  }
  if (!quote || !body) return fail("原文和批注内容都不能为空");
  if (body.length > 4000) return fail("批注太长");
  if (quote.length > 2000) return fail("选中的原文太长");

  const visibility = payload.visibility === "public" ? "public" : "private";
  const now = new Date().toISOString();

  const row = await env.DB.prepare(
    `INSERT INTO notes (slug, block, start, end, quote, prefix, suffix, body, visibility, status, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
     RETURNING *`
  )
    .bind(
      slug,
      block,
      start,
      typeof end === "number" ? end : start + quote.length,
      quote,
      payload.prefix || "",
      payload.suffix || "",
      body,
      visibility,
      now,
      now
    )
    .first();

  return json({ note: rowToNote(row) }, 201);
}

async function updateNote(id, request, env) {
  const payload = await request.json().catch(() => null);
  if (!payload) return fail("请求体不是合法 JSON");

  const fields = [];
  const args = [];
  if (typeof payload.body === "string" && payload.body.trim()) {
    fields.push("body = ?");
    args.push(payload.body.trim());
  }
  if (payload.visibility === "public" || payload.visibility === "private") {
    fields.push("visibility = ?");
    args.push(payload.visibility);
  }
  if (["open", "applied", "dismissed"].includes(payload.status)) {
    fields.push("status = ?");
    args.push(payload.status);
  }
  if (typeof payload.resolution === "string") {
    fields.push("resolution = ?");
    args.push(payload.resolution.trim());
  }
  if (!fields.length) return fail("没有要更新的字段");

  fields.push("updated_at = ?");
  args.push(new Date().toISOString());
  args.push(id);

  const row = await env.DB.prepare(
    `UPDATE notes SET ${fields.join(", ")} WHERE id = ? RETURNING *`
  )
    .bind(...args)
    .first();

  if (!row) return fail("找不到这条批注", 404);
  return json({ note: rowToNote(row) });
}

async function deleteNote(id, env) {
  const row = await env.DB.prepare("DELETE FROM notes WHERE id = ? RETURNING id")
    .bind(id)
    .first();
  if (!row) return fail("找不到这条批注", 404);
  return json({ ok: true });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (!path.startsWith("/api/notes")) {
      return env.ASSETS ? env.ASSETS.fetch(request) : new Response("Not found", { status: 404 });
    }

    const isAuthor = authorized(request, env);
    const method = request.method;

    // 读：有令牌看全部，没令牌只看 public
    if (path === "/api/notes" && method === "GET") {
      return listNotes(url, request, env);
    }

    // 以下全部需要令牌
    if (!isAuthor) return fail("需要令牌", 401);

    if (path === "/api/notes" && method === "POST") {
      return createNote(request, env);
    }

    const match = path.match(/^\/api\/notes\/(\d+)$/);
    if (match) {
      const id = Number(match[1]);
      if (method === "PATCH") return updateNote(id, request, env);
      if (method === "DELETE") return deleteNote(id, env);
    }

    return fail("没有这个接口", 404);
  },
};
