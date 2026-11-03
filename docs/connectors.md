# Connectors and note saving

Each person connects their own accounts. Nothing in the code or the default installer names or contains an account, and tokens live in Windows Credential Manager via `keyring` (`notion_oauth_token`, `google_oauth_token`).

Set `ESCORT_CONNECTORS=mock` (the default when nothing is connected) to use the fictional BayaniPay calendar, Notion and Google fixtures instead.

## Notion

Create an integration at https://www.notion.so/my-integrations, share the pages or databases you want with it, then connect Notion in Escort (the token is saved as `notion_oauth_token`).

## Google

### App credentials

In Google Cloud Console, create a project and an OAuth client of type **Desktop app**.

- **Enable these APIs:** Google Calendar, Docs, Sheets, Slides, Drive and Picker.
- **OAuth consent screen:** declare the scopes below. `drive.readonly` is a restricted scope, which is fine while the app is in **Testing**: add every user under **Test users**.
- **API key (only for the Drive file chooser):** create an API key and restrict it to the **Google Picker API**.

Nothing is hardcoded. Escort looks for the client ID, secret and API key in this order:

1. Environment: `ESCORT_GOOGLE_CLIENT_ID`, `ESCORT_GOOGLE_CLIENT_SECRET`, `ESCORT_GOOGLE_API_KEY`
2. Settings > Connect accounts > Google > "Google app credentials" (ID in `config.json`, secret and key in the keyring)
3. `%APPDATA%\Escort\google_client.json` (`~/.config/Escort/google_client.json` off Windows)
4. A `google_client.json` next to `Escort.exe`

The secret and key always come from the same source as the client ID. Both files use this format (`api_key` is optional):

```json
{"client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com", "client_secret": "YOUR_CLIENT_SECRET", "api_key": "YOUR_PICKER_API_KEY"}
```

The installer bundles `google_client.json` only when you opt in: `ISCC /DBundleGoogleClient=1 packaging\escort.iss`, with a git-ignored `google_client.json` in the repo root. `/api/status` reports only whether credentials are configured and where they came from; the secret and key are never returned or logged.

### Sign-in and scopes

**Connect Google** opens your own browser for a standard PKCE loopback sign-in. Tokens refresh on their own. Everything past sign-in is asked for with incremental consent (`include_granted_scopes=true`) the first time you use it, so Google shows only the new permission.

| Capability | Scopes (under `https://www.googleapis.com/auth/`) | Asked when |
| --- | --- | --- |
| Calendar, Docs links, Drive import | `calendar.readonly`, `documents.readonly`, `drive.readonly` | Connect Google |
| Sheets links | `spreadsheets.readonly` | First Sheets link (**Allow Sheets access**) |
| Slides links | `presentations.readonly` | First Slides link (**Allow Slides access**) |
| Save notes to Drive | `drive.file`, `documents` | Turning note saving on (**Allow note saving**) |

Escort never asks for the full `drive` scope.

### Importing from Google

- **Paste a link** to a Doc, Sheet or Slides deck. Escort reads that one file and splits it into cited sections, like the Notion importer:
  - Docs: headings, paragraphs, lists and tables.
  - Sheets: one table per tab, capped at 200 rows x 30 columns and 20 tabs, with a note when something was cut.
  - Slides: one section per slide with text from shapes, tables and groups, plus speaker notes.
- **Import from Google Drive** lets you search your Drive, or paste a Drive **file or folder link**. A folder is walked up to 3 levels deep (at most 40 files), and every supported file is pre-selected. PDF, DOCX, TXT and MD are downloaded; Google Docs, Sheets and Slides are exported. Files over 15 MB are refused.

Errors say what to do: a file not shared with the account, a disabled API (with a link to enable it), a missing permission (with the right **Allow … access** button, which retries once Google grants it), or an expired session.

## Saving meeting notes

Off by default. Escort only writes when you click **Note it** on a commitment card or **Save to Google Docs / Save to Notion** on the recap. Prep chat understands "save this to my notes", but only saves after you reply yes. Saving again appends a new dated section; nothing is overwritten.

**Google Docs.** Turn on **Save notes to Drive** in Connections > Google. Escort creates one `Escort` folder and one Doc per meeting, titled `Escort · <meeting> · <YYYY-MM-DD>`, and refuses to edit any doc it did not create. To remove access, use https://myaccount.google.com/permissions.

**Notion.** Give your integration the **Insert content** and **Update content** capabilities and share a parent page with it. Turn on **Save notes to Notion** in Connections > Notion and pick that page. Escort creates one child page per meeting under it.

**Keep an existing Notion page updated.** In Connections > Notion, use **Choose a page to keep updated**. Escort only ever writes to pages you picked or pages it created; every other page id is refused before a request is made. Updates are targeted (append under a heading such as **Commitments**, or replace one block) and always show a preview with Confirm and Cancel first.

### API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/notes/drive`, `POST /api/notes/notion` | Save `{kind: "commitment" \| "summary", card_id?}`; returns `{ok, doc: {id, url, title}}`. `GET` returns the current meeting's doc or page. |
| `POST /api/connectors/google/notes`, `POST /api/connectors/notion/notes` | Turn note saving on or off (`{enabled, parent_id?, parent_title?}` for Notion) |
| `POST /api/connectors/google/consent` | Ask for a capability: `notes` (default), `docs`, `sheets`, `slides`, `drive_browse` |
| `POST /api/connectors/notion/targets` | `{add: {id, title}}` or `{remove: id}` for pages to keep updated |
| `POST /api/notes/notion/update/preview` | `{page_id, kind, card_id?, block_id?, text?}` returns `{plan_id, summary, lines}` and writes nothing |
| `POST /api/notes/notion/update/confirm`, `.../cancel` | Perform or discard a previewed `{plan_id}` |
| `POST /api/context/drive-link` | Resolve a pasted Drive file or folder link to importable files |
