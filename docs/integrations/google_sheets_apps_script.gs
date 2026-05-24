/**
 * Google Apps Script webhook receiver for Avito Watcher alerts.
 *
 * Setup:
 * 1) Paste into Extensions -> Apps Script in your target spreadsheet.
 * 2) Optionally set Script Property WEBHOOK_SECRET.
 * 3) Deploy as Web App and set URL into GOOGLE_SHEETS_WEBHOOK_URL.
 */

const DEFAULT_SHEET_NAME = 'Alerts';
const FALLBACK_WEBHOOK_SECRET = ''; // Prefer Script Properties over hardcoding.

const COLUMNS = [
  'sent_at',
  'search_name',
  'external_id',
  'title',
  'price',
  'area_m2',
  'rooms',
  'address',
  'published_label',
  'published_at',
  'url',
  'score',
  'tags',
  'summary',
  'message',
];

function doPost(e) {
  try {
    const payload = parsePayload_(e);
    validateSecret_(payload.secret);

    const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
    const sheetName = getConfiguredSheetName_() || DEFAULT_SHEET_NAME;
    const sheet = spreadsheet.getSheetByName(sheetName) || spreadsheet.insertSheet(sheetName);

    ensureHeader_(sheet);
    sheet.appendRow(mapRow_(payload));

    return jsonResponse_({ ok: true });
  } catch (err) {
    return jsonResponse_({ ok: false, error: safeError_(err) });
  }
}

function parsePayload_(e) {
  if (!e || !e.postData || !e.postData.contents) {
    throw new Error('Missing request body');
  }
  return JSON.parse(e.postData.contents);
}

function validateSecret_(providedSecret) {
  const expectedSecret = getConfiguredSecret_();
  if (!expectedSecret) {
    return;
  }
  if (!providedSecret || providedSecret !== expectedSecret) {
    throw new Error('Unauthorized');
  }
}

function getConfiguredSecret_() {
  const props = PropertiesService.getScriptProperties();
  return props.getProperty('WEBHOOK_SECRET') || FALLBACK_WEBHOOK_SECRET;
}

function getConfiguredSheetName_() {
  const props = PropertiesService.getScriptProperties();
  return props.getProperty('SHEET_NAME');
}

function ensureHeader_(sheet) {
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(COLUMNS);
    return;
  }

  const firstRow = sheet.getRange(1, 1, 1, COLUMNS.length).getValues()[0];
  const missingHeaders = COLUMNS.some((name, i) => firstRow[i] !== name);
  if (missingHeaders) {
    sheet.insertRowBefore(1);
    sheet.getRange(1, 1, 1, COLUMNS.length).setValues([COLUMNS]);
  }
}

function mapRow_(payload) {
  return [
    valueOrEmpty_(payload.sent_at),
    valueOrEmpty_(payload.search_name),
    valueOrEmpty_(payload.external_id),
    valueOrEmpty_(payload.title),
    valueOrEmpty_(payload.price),
    valueOrEmpty_(payload.area_m2),
    valueOrEmpty_(payload.rooms),
    valueOrEmpty_(payload.address),
    valueOrEmpty_(payload.published_label),
    valueOrEmpty_(payload.published_at),
    valueOrEmpty_(payload.url),
    valueOrEmpty_(payload.score),
    normalizeTags_(payload.tags),
    valueOrEmpty_(payload.summary),
    valueOrEmpty_(payload.message),
  ];
}

function normalizeTags_(tags) {
  if (Array.isArray(tags)) {
    return tags.join(', ');
  }
  return valueOrEmpty_(tags);
}

function valueOrEmpty_(value) {
  if (value === null || value === undefined) {
    return '';
  }
  return String(value);
}

function jsonResponse_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function safeError_(err) {
  if (!err || !err.message) {
    return 'Unknown error';
  }
  return err.message;
}
