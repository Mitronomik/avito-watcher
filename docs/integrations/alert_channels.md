# Alert channels integration kit

This project supports multiple outbound alert channels through `CompositeNotifier` and the `ALERT_CHANNELS` env variable.

## ALERT_CHANNELS syntax

`ALERT_CHANNELS` is a comma-separated list of channel names, in send order.

Examples:

- `jsonl`
- `jsonl,email`
- `jsonl,google_sheets`
- `jsonl,telegram`
- `jsonl,email,google_sheets`

Recommended development mode:

```env
ALERT_CHANNELS=jsonl
```

Production candidates (pick what your installation supports):

- `ALERT_CHANNELS=jsonl,telegram`
- `ALERT_CHANNELS=jsonl,email`
- `ALERT_CHANNELS=jsonl,google_sheets`

## Channel behavior and ownership

### jsonl (safest default)

- Local durable outbox.
- Writes every alert to `JSONL_OUTBOX_PATH`.
- Safest channel for smoke testing and audits.
- Usually should stay enabled in all environments as an audit trail.

### email (SMTP, per installation)

- Uses operator-provided SMTP credentials.
- Each installation configures its own SMTP provider/account.
- The repository does not ship shared email credentials.

### google_sheets (webhook, per spreadsheet/user)

- Sends webhook payload to `GOOGLE_SHEETS_WEBHOOK_URL`.
- Receiver is typically a Google Apps Script deployed by each operator in their own Google account/sheet.
- The repository ships an example receiver script, not a shared hosted endpoint.

### telegram (bot, per installation)

- Uses operator-owned bot token/chat id.
- Each installation configures its own bot and destination chat.

## Google Sheets setup

### 1) Create and prepare your sheet

1. Create a Google Sheet in the Google account that will receive alerts.
2. Open **Extensions → Apps Script**.
3. Paste script from `docs/integrations/google_sheets_apps_script.gs`.
4. Configure secret:
   - set script property `WEBHOOK_SECRET`, or
   - leave script-level fallback constant for local testing.
5. Save script.

### 2) Deploy Apps Script as Web App

1. Click **Deploy → New deployment**.
2. Type: **Web app**.
3. **Execute as**: `Me`.
4. **Who has access**: choose access level appropriate for your security model.
5. Deploy and copy the Web App URL.

### 3) Configure `.env`

```env
ALERT_CHANNELS=jsonl,google_sheets
GOOGLE_SHEETS_WEBHOOK_ENABLED=true
GOOGLE_SHEETS_WEBHOOK_URL=https://script.google.com/macros/s/.../exec
GOOGLE_SHEETS_WEBHOOK_SECRET=your-shared-secret
```

### 4) Smoke test

Run one monitoring pass and then verify:

- records appear in JSONL outbox (`JSONL_OUTBOX_PATH`), and
- rows are appended to your Google Sheet.

Example command:

```bash
python -m app.cli run-once
```

## Email setup

Email channel uses SMTP from `.env`.

Required configuration example:

```env
ALERT_CHANNELS=jsonl,email
EMAIL_ENABLED=true
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USERNAME=alerts@example.com
SMTP_PASSWORD=app-password-or-smtp-password
EMAIL_FROM=alerts@example.com
EMAIL_TO=you@example.com
```

Notes:

- Port `465` uses `SMTP_SSL`.
- Port `587` uses `STARTTLS`.
- Some providers require app passwords (for example when MFA is enabled).

Smoke command:

```bash
python -m app.cli run-once
```

Then confirm:

- JSONL received alert lines,
- destination mailbox received alert email.

## Who owns the integration

- **Google Sheets integration** is owned by each parser operator:
  - they deploy Apps Script in their own Google account/sheet, unless they run their own hosted webhook endpoint.
- **Email integration** is owned by each parser operator:
  - they provide SMTP credentials for their own mail provider/account.
- **Repository responsibility**:
  - examples, docs, and channel wiring,
  - no shared credentials,
  - no shared operator-specific webhook infrastructure.

## Safer diagnostics

- Runtime/admin outputs may show enabled channel names.
- Runtime/admin outputs must not expose secrets (SMTP password, webhook secret, bot token).
- If a channel is enabled but not fully configured, keep `jsonl` enabled to preserve observable local delivery while fixing config.
