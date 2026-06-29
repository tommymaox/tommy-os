# Wiki Tab Spec

**Tab:** Wiki  
**Nav icon:** book-open  
**Status:** Live

---

## Purpose

Tommy's second brain / personal knowledge base. Claude acts as the wiki agent — ingesting, organising, and retrieving knowledge across all life domains.

---

## Storage

Wiki pages are `.md` files on the host at `/home/tommy/zuyu/wiki/`. They are bind-mounted into the Docker container at `/wiki`. This means:

- Claude can read/write files directly at `/home/tommy/zuyu/wiki/`
- The webapp serves them via the FastAPI backend reading `/wiki/`
- Files survive container rebuilds

### Volume mount (required in Docker run command):
```
-v /home/tommy/zuyu/wiki:/wiki
```

---

## File Format

```markdown
---
title: Page Title
category: fitness
tags: [running, marathon]
created: 2026-04-10
updated: 2026-04-10
summary: One-line description of the page
---

# Page Title

Content in markdown...
```

---

## Categories

`fitness` `career` `learning` `finance` `people` `projects` `ideas` `reference`

---

## Directory Structure

```
wiki/
  CLAUDE.md          ← Agent schema & instructions
  fitness/
    melbourne-half-marathon.md
  career/
    ccnp-study-notes.md
  learning/
  finance/
  people/
  projects/
  ideas/
  reference/
    docker-commands.md
```

---

## API Routes

| Method | Path | Description |
|---|---|---|
| GET | `/api/wiki/pages` | List all pages (no content) |
| GET | `/api/wiki/pages/{slug}` | Get page with content |
| POST | `/api/wiki/pages` | Create new page |
| PUT | `/api/wiki/pages/{slug}` | Update page |
| DELETE | `/api/wiki/pages/{slug}` | Delete page |
| GET | `/api/wiki/search?q=` | Full-text search |

Slug format: `category/filename` e.g. `fitness/melbourne-half-marathon`

---

## Frontend

Two-panel layout:
- **Left sidebar**: search + category-grouped page list + New Page button
- **Right main**: topbar (title, tags, updated date, Edit/Delete buttons) + markdown-rendered content

Markdown rendered with `marked.js` (CDN).

---

## Claude as Wiki Agent

See `/home/tommy/zuyu/wiki/CLAUDE.md` for the full agent schema. In short:

- Search existing pages before creating new ones
- Append to existing pages, don't duplicate
- Always update `updated` date in frontmatter
- Files accessible at `/home/tommy/zuyu/wiki/**/*.md`
