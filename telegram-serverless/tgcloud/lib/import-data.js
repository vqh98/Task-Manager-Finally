import { db } from 'sdk';

// Import is intentionally limited to known application tables and safe SQL
// identifiers. This module is invoked through the authenticated CLI, never by
// a Telegram update or public Mini App endpoint.
const ALLOWED_TABLES = new Set([
  'tasks', 'task_deadline_alerts', 'meetings', 'reminders',
  'meeting_recurrence_series', 'meeting_recurrence_occurrences', 'users',
  'access_codes', 'access_code_usages', 'team_members', 'manager_profiles',
  'event_shares', 'task_shares', 'shared_alert_settings', 'shared_alert_log',
  'task_reports', 'telegram_accounts', 'task_progress_history', 'user_settings',
  'meeting_quiet_hours', 'notification_log', 'app_migrations',
  'recurrence_series', 'recurrence_occurrences', 'google_connections',
  'google_oauth_states', 'google_meeting_links', 'google_sync_queue',
]);

const IDENTIFIER = /^[a-z][a-z0-9_]*$/;

export default async function importData(input) {
  const table = input?.table;
  const rows = input?.rows;
  if (!ALLOWED_TABLES.has(table)) throw new Error(`Unsupported table: ${table}`);
  if (!Array.isArray(rows) || rows.length === 0 || rows.length > 100) {
    throw new Error('rows must contain between 1 and 100 records');
  }

  let imported = 0;
  for (const row of rows) {
    const columns = Object.keys(row);
    if (!columns.length || columns.some((name) => !IDENTIFIER.test(name))) {
      throw new Error('Invalid column name');
    }
    const params = {};
    const placeholders = columns.map((name, index) => {
      const key = `:v${index}`;
      params[key] = row[name];
      return key;
    });
    const query = `INSERT OR REPLACE INTO ${table} (${columns.join(',')}) VALUES (${placeholders.join(',')})`;
    await db.run(query, params);
    imported += 1;
  }
  return { ok: true, table, imported };
}
