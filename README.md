# pull_docket_comments.py

Download public comments for a [regulations.gov](https://www.regulations.gov) docket using the official **API v4**.

This script follows the documented retrieval path: a docket contains documents; comments are listed **per document** with `filter[commentOnId]` set to that document’s **`objectId`** (not its `documentId`). Comment *text* and most submitter fields come from the comment **detail** endpoint, not the list endpoint.

Official sources:

- Docs: https://open.gsa.gov/api/regulationsgov/
- OpenAPI: https://open.gsa.gov/api/regulationsgov/v4/openapi.yaml
- API keys / default rate limits: https://api.data.gov/signup/ and https://api.data.gov/docs/developer-manual/

## Ontology (from official docs, not inferred product lore)

```
Docket  ("an organizational folder containing multiple documents")
  └── Documents  (documentType enum in OpenAPI)
        Notice | Rule | Proposed Rule | Supporting & Related Material | Other
          └── Comments  listed with filter[commentOnId] = document.attributes.objectId
                └── Comment detail  GET /v4/comments/{commentId}?include=attachments
```

- Docket types (OpenAPI `DocketType`): `Rulemaking`, `Nonrulemaking`.
- The narrative API description lists document types as “Proposed Rule, Rule, Supporting & Related, or Other.” The OpenAPI `DocumentType` enum also includes **`Notice`** and spells the supporting type **`Supporting & Related Material`**. The script accepts whatever `documentType` the API returns.
- Comments are **not** listed with `filter[docketId]`. That filter exists on `/v4/documents`, not on `/v4/comments` (OpenAPI). The documented examples first list documents for the docket, then list comments for each document’s `objectId`.
- A comment **detail** record does carry `docketId` (docs: always publicly viewable). That tells you which docket the comment belongs to. This research did **not** find a documented API to list comments “on the docket itself” (for example using the docket’s `objectId` as `commentOnId`).
- List vs detail: the OpenAPI list schema (`Comment`) has identifiers, dates, and title — not the comment body. The detail schema (`CommentDetail` / `BasicDetailModel`) includes `comment` text, `receiveDate`, `commentOnDocumentId`, and agency-configurable submitter fields (`firstName`, `lastName`, `organization`, and others). Agencies can hide some of those fields.
- Attachments: not included by default. Pass `include=attachments` on the detail endpoint. Files are described as `fileFormats[]` with `fileUrl` (OpenAPI: “URL of the file on S3”), `format`, and `size`.

Pagination (OpenAPI, same for documents/comments/dockets): `page[size]` 5–250, `page[number]` 1–20 (at most 5,000 rows per query). For more than 5,000 comments, official examples sort `lastModifiedDate,documentId` and continue with `filter[lastModifiedDate][ge]=…` (format `yyyy-MM-dd HH:mm:ss`, illustrated in Eastern time). Official FAQ: that `lastModifiedDate` parameter is **beta** and may be removed when a permanent bulk download exists. There is no official bulk-download API today.

## Test / staging environment

The official FAQ (“What is the staging API url?”) says:

> Users should be able to access our staging API at https://api-staging.regulations.gov. Please use this environment for testing purposes.

It does **not** say the staging API is keyless. Direct checks (15 November 2026, America/Chicago) found:

- No API key → HTTP 403, `API_KEY_MISSING` (“No api_key was supplied”).
- `DEMO_KEY` (header or `api_key` query) → HTTP 503, body `no healthy upstream` (staging was not healthy at test time).
- Staging `DEMO_KEY` responses included `X-RateLimit-Limit: 10` (stricter than production defaults documented for api.data.gov).

Use `--base-url https://api-staging.regulations.gov/v4` only when staging is up **and** you have a key that staging accepts. For exploration of production data, use the default base URL and a key.

## DEMO_KEY

Official docs: you may send `DEMO_KEY` in `X-Api-Key` “for sample purposes,” and “should only be used for demonstration purposes… not… for anything more than exploring our APIs.”

api.data.gov default limits (https://api.data.gov/docs/developer-manual/):

- Personal key: 1,000 requests per hour (rolling window), across all api.data.gov APIs for that key.
- `DEMO_KEY`: **30 requests per IP per hour** and **50 per IP per day**.
- Over limit: HTTP 429. Inspect `X-RateLimit-Limit` and `X-RateLimit-Remaining`.

The script defaults to `DEMO_KEY` if `REGS_API_KEY` is unset, prints a warning, and backs off on 429 / low remaining counts. If a 429 includes a `Retry-After` of more than 180 seconds (quota reset, sometimes several hours), the script stops with an error instead of sleeping that long; rerun the same `--out` after the window resets (already-saved files are skipped).

Observed during a 15 November 2026 (America/Chicago) `DEMO_KEY` test against production: responses advertised `X-RateLimit-Limit: 10` (not the documented 30/hour), and once remaining hit 0 a subsequent request returned HTTP 429 with `Retry-After` of 25921 seconds (~7.2 hours). Treat the documented 30/hour and 50/day figures as the official policy, but also honor the headers you actually receive.

For a real docket pull, set your own key:

```bash
export REGS_API_KEY='your-api.data.gov-key'
```

Signup: https://api.data.gov/signup/ (also embedded on the official regulations.gov API docs page).

## Install

```bash
cd /workspace/regs_comments
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
# Explore with DEMO_KEY (low limits). Cap detail fetches for a dry run:
python pull_docket_comments.py \
  --docket FDA-2025-N-3262 \
  --out ./out-fda-2025-n-3262 \
  --max-comments 3 \
  --max-attachments 1

# Production pull with your key (downloads all comment details and attachments):
export REGS_API_KEY='your-key'
python pull_docket_comments.py \
  --docket FDA-2025-N-3262 \
  --out ./out-fda-2025-n-3262

# If staging is healthy and your key works there:
python pull_docket_comments.py \
  --docket FDA-2025-N-3262 \
  --base-url https://api-staging.regulations.gov/v4 \
  --out ./out-staging \
  --max-comments 3
```

| Flag | Meaning |
| --- | --- |
| `--docket` | Required docket ID, e.g. `FDA-2025-N-3262` |
| `--base-url` | API root, default `https://api.regulations.gov/v4` |
| `--out` | Output directory, default `./out-<docket>` |
| `--no-attachments` | Do not request or download attachments |
| `--max-comments` | Stop after this many *new* comment-detail fetches (already-saved comments are skipped). Useful with `DEMO_KEY`. |
| `--max-attachments` | Stop downloading files after this many attachments (details are still saved). |
| `--document-id` | Repeatable. Only look up comments for these document IDs (all documents are still listed). |
| `--open-for-comment-only` | Only look up comments on documents with `openForComment=true`. Use with `DEMO_KEY` to avoid one list call per supporting document. |

The run is **resumable**: comment JSON already present under `raw/` is not fetched again. Re-run the same `--out` after a rate-limit pause or with a production key to continue.

## Output

```
<out>/
  docket.json           # GET /dockets/{id}
  documents.json        # all listed documents (id, type, objectId, title, …)
  comment_counts.json   # per-document comment totals from list metadata
  comments.csv          # one row per fetched comment detail
  raw/<commentId>.json  # full detail JSON
  attachments/<commentId>/…
```

`comments.csv` columns: `id`, `documentId` (parent document / `commentOnDocumentId`), `title`, `postedDate`, `receiveDate`, `firstName`, `lastName`, `organization`, `comment`, `attachmentCount`.
