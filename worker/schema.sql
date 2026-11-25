-- 批注表
-- 用 wrangler d1 execute draftrun-notes --remote --file worker/schema.sql 执行

CREATE TABLE IF NOT EXISTS notes (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  slug       TEXT    NOT NULL,              -- 文章 slug
  block      INTEGER NOT NULL,              -- 第几个顶层块（构建时打上的 data-block）
  start      INTEGER NOT NULL,              -- 块内起止字符偏移
  end        INTEGER NOT NULL,
  quote      TEXT    NOT NULL,              -- 选中的原文
  prefix     TEXT,                          -- 前文，用于消歧
  suffix     TEXT,                          -- 后文
  body       TEXT    NOT NULL,              -- 批注内容
  visibility TEXT    NOT NULL DEFAULT 'private',  -- private / public
  status     TEXT    NOT NULL DEFAULT 'open',     -- open / applied / dismissed
  resolution TEXT,                          -- 处理结果一句话
  created_at TEXT    NOT NULL,
  updated_at TEXT    NOT NULL
);

-- 按文章取列表、按位置排序，走这个索引，避免全表扫描
CREATE INDEX IF NOT EXISTS idx_notes_slug ON notes (slug, block, start);
