import { db } from 'sdk';

const TABLES = [
  'tasks', 'task_deadline_alerts', 'meetings', 'reminders',
  'meeting_recurrence_series', 'meeting_recurrence_occurrences', 'users',
  'access_codes', 'access_code_usages', 'team_members', 'manager_profiles',
  'event_shares', 'task_shares', 'shared_alert_settings', 'shared_alert_log',
  'task_reports', 'telegram_accounts', 'task_progress_history', 'user_settings',
  'meeting_quiet_hours', 'notification_log', 'app_migrations',
  'recurrence_series', 'recurrence_occurrences', 'google_connections',
  'google_oauth_states', 'google_meeting_links', 'google_sync_queue',
];

export default async function tableCounts() {
  const counts = {};
  for (const table of TABLES) {
    const row = await db.get(`SELECT COUNT(*) AS count FROM ${table}`);
    counts[table] = Number(row?.count ?? 0);
  }
  return counts;
}
