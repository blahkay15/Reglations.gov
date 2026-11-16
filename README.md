# pull_docket_comments.py

Download public comments for a [regulations.gov](https://www.regulations.gov) document using the official **API v4**, including attachment files.

The usual way to run it is a **document ID** (repeat it for more than one document):

```text
python pull_docket_comments.py --document-id FDA-2025-N-3262-0001
```

That loads the document with `GET /v4/documents/{documentId}`, reads its `objectId`, lists comments with `filter[commentOnId]={objectId}`, then saves each comment from `GET /v4/comments/{commentId}?include=attachments` and downloads the attachment files.

`--docket` is the other entry point. It lists the docket and, by default, pulls **Proposed Rule** documents only. `--all-documents` restores the old “every document in the docket” behavior. Before any comment file is downloaded, the script prints each selected document and that document’s `meta.totalElements` comment count.

Official sources:

- Docs: https://open.gsa.gov/api/regulationsgov/
- OpenAPI: https://open.gsa.gov/api/regulationsgov/v4/openapi.yaml
- API keys: https://api.data.gov/signup/

Do not commit an API key. Set it in the shell for the session. `.env` is listed in `.gitignore`.

## Quick start (Windows PowerShell)

From the folder that contains this README:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:REGS_API_KEY = "your-key"
python pull_docket_comments.py --document-id FDA-2025-N-3262-0001
```

If activation is blocked (`running scripts is disabled on this system`), allow local scripts for your user, then activate again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
```

Small test run (five new comment details, then stop). Already-saved comments are skipped, so you can rerun the same command to continue:

```powershell
python pull_docket_comments.py --document-id FDA-2025-N-3262-0001 --max-comments 5
```

More than one document:

```powershell
python pull_docket_comments.py --document-id FDA-2025-N-3262-0001 --document-id FDA-2025-N-3262-0002
```

Proposed Rules in a docket, or every document type:

```powershell
python pull_docket_comments.py --docket FDA-2025-N-3262
python pull_docket_comments.py --docket FDA-2025-N-3262 --all-documents
```

Output goes to `out-FDA-2025-N-3262` (the document’s docket id, or `--docket` when you pass it). `comments.csv` is UTF-8 with a BOM so Excel opens it correctly.

## Command Prompt

```bat
py -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
set REGS_API_KEY=your-key
python pull_docket_comments.py --document-id FDA-2025-N-3262-0001
```

Test run:

```bat
python pull_docket_comments.py --document-id FDA-2025-N-3262-0001 --max-comments 5
```

## macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export REGS_API_KEY='your-key'
python pull_docket_comments.py --document-id FDA-2025-N-3262-0001
```

Test run:

```bash
python pull_docket_comments.py --document-id FDA-2025-N-3262-0001 --max-comments 5
```

`tzdata` (in `requirements.txt`) supplies the Eastern-time database Windows needs when a document has more than 5,000 comments. On macOS and Linux the system timezone data is used as well.

## Flags

| Flag | Meaning |
| --- | --- |
| `--document-id` | Document ID, e.g. `FDA-2025-N-3262-0001`. Repeat the flag for another document. This is the primary way to run the script. |
| `--docket` | Docket ID, e.g. `FDA-2025-N-3262`. Pulls Proposed Rule documents only, unless `--all-documents` is set. Optional when `--document-id` is given. |
| `--all-documents` | With `--docket`, pull every document type (Supporting & Related Material, Other, and the rest). |
| `--base-url` | API root. Default `https://api.regulations.gov/v4`. |
| `--out` | Output directory. Default `./out-<docket>`. |
| `--no-attachments` | Do not request or download attachments. |
| `--max-comments` | Stop after this many *new* comment-detail fetches. Already-saved comments are skipped. |
| `--max-attachments` | Stop downloading files after this many attachments. Details are still saved. |
| `--open-for-comment-only` | Only pull documents with `openForComment=true`. |

The run is **resumable**. Comment JSON already under `raw/` is not fetched again, and attachment files that are already on disk are skipped. Re-run the same `--out` after a rate-limit pause.

## Output

```text
<out>/
  docket.json
  documents.json
  comment_counts.json          # one totalElements per selected document
  comments.csv                  # UTF-8 BOM, one row per saved comment
  raw/<commentId>.json
  attachments/<commentId>/...
```

`comments.csv` columns: `id`, `documentId` (parent document / `commentOnDocumentId`), `title`, `postedDate`, `receiveDate`, `firstName`, `lastName`, `organization`, `comment`, `attachmentCount`.

File names are rewritten so Windows accepts them (`:` `*` `?` `"` `<` `>` `|` and other illegal characters are removed, reserved names such as `CON` are avoided, and very long paths are shortened).

## How comments are found

```text
Docket
  └── Documents
        Notice | Rule | Proposed Rule | Supporting & Related Material | Other
          └── Comments  filter[commentOnId] = document.attributes.objectId
                └── Comment detail  GET /v4/comments/{commentId}?include=attachments
```

- `--document-id` does not list the rest of the docket. It loads that document and uses its `objectId`.
- `--docket` lists documents, keeps `documentType` **Proposed Rule**, and prints each one’s comment total before downloading. `--all-documents` keeps every type.
- Comments are not listed with `filter[docketId]`. That filter is on `/v4/documents`, not `/v4/comments`.
- The list payload has identifiers, dates, and title. The comment text and submitter fields are on the detail record. Agencies can hide some submitter fields.
- Attachments are omitted unless `include=attachments` is set. Files are `fileFormats[]` with `fileUrl`, `format`, and `size`.

Pagination is the same for documents and comments: `page[size]` 5–250 and `page[number]` 1–20 (at most 5,000 rows per query). For more than 5,000 comments the script sorts `lastModifiedDate,documentId` and continues with `filter[lastModifiedDate][ge]` (`yyyy-MM-dd HH:mm:ss` in Eastern time), which is the documented approach. That parameter is beta and may be removed when a bulk download exists.

## API key and DEMO_KEY

With no `REGS_API_KEY`, the script sends `DEMO_KEY` and prints a warning. Official api.data.gov limits for `DEMO_KEY` are 30 requests per IP per hour and 50 per IP per day. A full docket is larger than that. Use your own key for a real pull.

Personal keys are 1,000 requests per hour (rolling window). HTTP 429 means the limit was hit. The script reads `Retry-After`. If that wait is longer than 180 seconds it stops instead of sleeping for hours; rerun the same `--out` after the window resets.

Signup: https://api.data.gov/signup/

## Tests

Offline checks (no API key and no network):

```bash
python -m unittest discover -s tests -v
```
