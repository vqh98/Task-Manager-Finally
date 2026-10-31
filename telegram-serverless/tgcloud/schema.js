import {
  table, integer, text, boolean, index, uniqueIndex, check, sql,
} from 'sdk/db';

const id = () => integer('id').primaryKey({ autoIncrement: true });
const createdAt = () => text('created_at').notNull().default(sql`CURRENT_TIMESTAMP`);

export const tasks = table('tasks', {
  id: id(), userId: integer('user_id').notNull(), title: text('title').notNull(),
  deadline: text('deadline').notNull(), priority: integer('priority').notNull(),
  assignee: text('assignee').notNull().default(''), progress: integer('progress').notNull().default(0),
  lastNote: text('last_note').notNull().default(''), status: text('status').notNull().default('open'),
  createdAt: createdAt(), updatedAt: text('updated_at').notNull().default(sql`CURRENT_TIMESTAMP`),
  lastChangedBy: integer('last_changed_by'), assigneeUserId: integer('assignee_user_id'),
  ccManagerId: integer('cc_manager_id'), completedAt: text('completed_at'),
  reportWarningSentAt: text('report_warning_sent_at'), reminderSentAt: text('reminder_sent_at'),
}, (t) => ({
  byUserDeadline: index('idx_tasks_user_deadline').on(t.userId, t.status, t.deadline, t.priority),
  priorityRange: check('chk_tasks_priority', sql`${t.priority} BETWEEN 1 AND 4`),
  progressRange: check('chk_tasks_progress', sql`${t.progress} BETWEEN 0 AND 100`),
}));

export const taskDeadlineAlerts = table('task_deadline_alerts', {
  taskId: integer('task_id').notNull(), warningDays: integer('warning_days').notNull(),
  notifiedAt: text('notified_at').notNull(),
}, (t) => ({ byTask: index('idx_task_deadline_alerts_task').on(t.taskId), uniqueAlert: uniqueIndex('uidx_task_deadline_alert').on(t.taskId, t.warningDays) }));

export const meetings = table('meetings', {
  id: id(), userId: integer('user_id').notNull(), title: text('title').notNull(),
  startsAt: text('starts_at').notNull(), endsAt: text('ends_at').notNull(),
  sourceText: text('source_text').notNull().default(''), createdAt: createdAt(),
}, (t) => ({ byUserStart: index('idx_meetings_user_start').on(t.userId, t.startsAt) }));

export const reminders = table('reminders', {
  id: id(), userId: integer('user_id').notNull(), title: text('title').notNull(),
  dueAt: text('due_at').notNull(), repeatType: text('repeat_type').notNull().default('once'),
  repeatUntil: text('repeat_until'), active: boolean('active').notNull().default(true),
  lastNotifiedAt: text('last_notified_at'), lastWarningSentForDueAt: text('last_warning_sent_for_due_at'),
  createdAt: createdAt(),
}, (t) => ({ byDue: index('idx_reminders_due').on(t.userId, t.active, t.dueAt) }));

export const meetingRecurrenceSeries = table('meeting_recurrence_series', {
  id: id(), userId: integer('user_id').notNull(), title: text('title').notNull(),
  sourceText: text('source_text').notNull().default(''), anchorStart: text('anchor_start').notNull(),
  anchorEnd: text('anchor_end').notNull(), interval: integer('interval').notNull().default(1),
  weekdays: text('weekdays'), frequency: text('frequency').notNull(), jalaliDay: integer('jalali_day'),
  endsOn: text('ends_on'), active: boolean('active').notNull().default(true), createdAt: createdAt(),
}, (t) => ({ byUser: index('idx_meeting_recurrence_user').on(t.userId, t.active) }));

export const meetingRecurrenceOccurrences = table('meeting_recurrence_occurrences', {
  seriesId: integer('series_id').notNull(), meetingId: integer('meeting_id').notNull(),
  occurrenceAt: text('occurrence_at').notNull(), skipped: boolean('skipped').notNull().default(false),
}, (t) => ({ uniqueOccurrence: uniqueIndex('uidx_meeting_occurrence').on(t.seriesId, t.occurrenceAt), uniqueMeeting: uniqueIndex('uidx_meeting_occurrence_meeting').on(t.meetingId) }));

export const users = table('users', {
  userId: integer('user_id').primaryKey(), role: text('role').notNull(),
  displayName: text('display_name').notNull().default(''), accessGranted: boolean('access_granted').notNull().default(false),
  subscriptionExpiresAt: text('subscription_expires_at'), createdAt: createdAt(),
});

export const accessCodes = table('access_codes', {
  code: text('code').primaryKey(), active: boolean('active').notNull().default(true),
  usageType: text('usage_type').notNull().default('single'), maxUses: integer('max_uses').notNull().default(1),
  redeemedBy: integer('redeemed_by'), redeemedAt: text('redeemed_at'), createdAt: createdAt(),
});

export const accessCodeUsages = table('access_code_usages', {
  code: text('code').notNull(), userId: integer('user_id').notNull(),
  displayName: text('display_name').notNull().default(''), redeemedAt: text('redeemed_at').notNull().default(sql`CURRENT_TIMESTAMP`),
}, (t) => ({ uniqueUsage: uniqueIndex('uidx_access_code_usage').on(t.code, t.userId), byCode: index('idx_access_code_usages_code').on(t.code, t.redeemedAt) }));

export const teamMembers = table('team_members', {
  managerId: integer('manager_id').notNull(), memberId: integer('member_id').notNull(),
  displayName: text('display_name').notNull().default(''), profilePhotoFileId: text('profile_photo_file_id'),
  active: boolean('active').notNull().default(true), createdAt: createdAt(),
}, (t) => ({ uniqueMember: uniqueIndex('uidx_team_member_pair').on(t.managerId, t.memberId), byMember: index('idx_team_member').on(t.memberId, t.active) }));

export const managerProfiles = table('manager_profiles', {
  userId: integer('user_id').primaryKey(), displayName: text('display_name').notNull(),
  updatedAt: text('updated_at').notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const eventShares = table('event_shares', {
  managerId: integer('manager_id').notNull(), memberId: integer('member_id').notNull(), permission: text('permission').notNull(),
  createdAt: createdAt(), updatedAt: text('updated_at').notNull().default(sql`CURRENT_TIMESTAMP`),
}, (t) => ({ uniqueShare: uniqueIndex('uidx_event_share').on(t.managerId, t.memberId), byMember: index('idx_event_shares_member').on(t.memberId, t.permission) }));

export const taskShares = table('task_shares', {
  managerId: integer('manager_id').notNull(), memberId: integer('member_id').notNull(),
  canView: boolean('can_view').notNull().default(false), canEdit: boolean('can_edit').notNull().default(false),
  canAdd: boolean('can_add').notNull().default(false), canDelete: boolean('can_delete').notNull().default(false),
  createdAt: createdAt(), updatedAt: text('updated_at').notNull().default(sql`CURRENT_TIMESTAMP`),
}, (t) => ({ uniqueShare: uniqueIndex('uidx_task_share').on(t.managerId, t.memberId), byMember: index('idx_task_shares_member').on(t.memberId) }));

export const sharedAlertSettings = table('shared_alert_settings', {
  memberId: integer('member_id').notNull(), managerId: integer('manager_id').notNull(),
  taskWarningDays: text('task_warning_days').notNull().default('1,3,7'),
  eventWarningMinutes: text('event_warning_minutes').notNull().default('60,1440'),
  enabled: boolean('enabled').notNull().default(true), updatedAt: text('updated_at').notNull().default(sql`CURRENT_TIMESTAMP`),
}, (t) => ({ uniqueSettings: uniqueIndex('uidx_shared_alert_settings').on(t.memberId, t.managerId) }));

export const sharedAlertLog = table('shared_alert_log', {
  memberId: integer('member_id').notNull(), managerId: integer('manager_id').notNull(), kind: text('kind').notNull(),
  itemId: integer('item_id').notNull(), threshold: integer('threshold').notNull(), sentAt: text('sent_at').notNull(),
}, (t) => ({ uniqueLog: uniqueIndex('uidx_shared_alert_log').on(t.memberId, t.managerId, t.kind, t.itemId, t.threshold) }));

export const taskReports = table('task_reports', {
  id: id(), taskId: integer('task_id').notNull(), managerId: integer('manager_id').notNull(),
  reporterId: integer('reporter_id').notNull(), progress: integer('progress').notNull(),
  note: text('note').notNull().default(''), createdAt: createdAt(),
}, (t) => ({ byTask: index('idx_task_reports_task').on(t.taskId, t.createdAt) }));

export const telegramAccounts = table('telegram_accounts', {
  userId: integer('user_id').primaryKey(), username: text('username'), displayName: text('display_name').notNull().default(''),
  updatedAt: text('updated_at').notNull().default(sql`CURRENT_TIMESTAMP`),
}, (t) => ({ uniqueUsername: uniqueIndex('idx_telegram_username').on(t.username) }));

export const taskProgressHistory = table('task_progress_history', {
  id: id(), taskId: integer('task_id').notNull(), managerId: integer('manager_id').notNull(),
  changedBy: integer('changed_by').notNull(), progress: integer('progress').notNull(), changedAt: text('changed_at').notNull(),
}, (t) => ({ byTask: index('idx_progress_history_task').on(t.taskId, t.changedAt, t.id) }));

export const userSettings = table('user_settings', {
  userId: integer('user_id').primaryKey(), digestHour: integer('digest_hour'), digestMinute: integer('digest_minute'),
  morningHour: integer('morning_hour'), morningMinute: integer('morning_minute'),
  digestEnabled: boolean('digest_enabled').notNull().default(true), morningEnabled: boolean('morning_enabled').notNull().default(true),
  deadlineWarningDays: integer('deadline_warning_days'), reportWarningHours: integer('report_warning_hours'),
  deadlineWarningDaysList: text('deadline_warning_days_list'), reminderWarningDaysList: text('reminder_warning_days_list'),
  reminderWarningEnabled: boolean('reminder_warning_enabled').notNull().default(false),
  reminderWarningHour: integer('reminder_warning_hour'), reminderWarningMinute: integer('reminder_warning_minute'),
  meetingDefaultDuration: integer('meeting_default_duration').notNull().default(60),
  meetingWindowStart: integer('meeting_window_start').notNull().default(480),
  meetingWindowEnd: integer('meeting_window_end').notNull().default(1140),
  updatedAt: text('updated_at').notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const meetingQuietHours = table('meeting_quiet_hours', {
  id: id(), userId: integer('user_id').notNull(), startMinute: integer('start_minute').notNull(),
  endMinute: integer('end_minute').notNull(), createdAt: createdAt(),
}, (t) => ({ byUser: index('idx_meeting_quiet_hours_user').on(t.userId, t.startMinute) }));

export const notificationLog = table('notification_log', {
  userId: integer('user_id').notNull(), kind: text('kind').notNull(), sentDate: text('sent_date').notNull(),
}, (t) => ({ uniqueNotification: uniqueIndex('uidx_notification_log').on(t.userId, t.kind, t.sentDate) }));

export const appMigrations = table('app_migrations', {
  name: text('name').primaryKey(), appliedAt: text('applied_at').notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const recurrenceSeries = table('recurrence_series', {
  id: id(), userId: integer('user_id').notNull(), title: text('title').notNull(), anchor: text('anchor').notNull(),
  priority: integer('priority').notNull(), assignee: text('assignee').notNull().default(''), note: text('note').notNull().default(''),
  interval: integer('interval').notNull().default(1), weekdays: text('weekdays'), frequency: text('frequency').notNull(),
  jalaliDay: integer('jalali_day'), endsOn: text('ends_on'), active: boolean('active').notNull().default(true),
});

export const recurrenceOccurrences = table('recurrence_occurrences', {
  seriesId: integer('series_id').notNull(), taskId: integer('task_id').notNull(), occurrenceAt: text('occurrence_at').notNull(),
  skipped: boolean('skipped').notNull().default(false),
}, (t) => ({ uniqueOccurrence: uniqueIndex('uidx_task_occurrence').on(t.seriesId, t.occurrenceAt) }));

export const googleConnections = table('google_connections', {
  userId: integer('user_id').primaryKey(), encryptedRefreshToken: text('encrypted_refresh_token').notNull(),
  accountEmail: text('account_email').notNull().default(''), calendarId: text('calendar_id').notNull().default('primary'),
  calendarName: text('calendar_name').notNull().default('تقویم اصلی'), enabled: boolean('enabled').notNull().default(true),
  connectedAt: text('connected_at').notNull().default(sql`CURRENT_TIMESTAMP`),
  updatedAt: text('updated_at').notNull().default(sql`CURRENT_TIMESTAMP`),
});

export const googleOauthStates = table('google_oauth_states', {
  state: text('state').primaryKey(), userId: integer('user_id').notNull(), expiresAt: text('expires_at').notNull(), createdAt: createdAt(),
});

export const googleMeetingLinks = table('google_meeting_links', {
  meetingId: integer('meeting_id').primaryKey(), userId: integer('user_id').notNull(), calendarId: text('calendar_id').notNull(),
  googleEventId: text('google_event_id').notNull(), syncedAt: text('synced_at').notNull().default(sql`CURRENT_TIMESTAMP`),
}, (t) => ({ byUser: index('idx_google_links_user').on(t.userId, t.calendarId) }));

export const googleSyncQueue = table('google_sync_queue', {
  id: id(), userId: integer('user_id').notNull(), meetingId: integer('meeting_id').notNull(), action: text('action').notNull(),
  calendarId: text('calendar_id').notNull().default(''), googleEventId: text('google_event_id').notNull().default(''),
  attempts: integer('attempts').notNull().default(0), availableAt: text('available_at').notNull().default(sql`CURRENT_TIMESTAMP`),
  lastError: text('last_error').notNull().default(''), createdAt: createdAt(),
}, (t) => ({ uniqueQueue: uniqueIndex('uidx_google_sync_queue').on(t.userId, t.meetingId, t.action, t.calendarId), ready: index('idx_google_sync_ready').on(t.availableAt, t.id) }));

export const conversationState = table('conversation_state', {
  userId: integer('user_id').primaryKey(), state: text('state').notNull(), data: text('data', { mode: 'json' }).notNull(),
  updatedAt: text('updated_at').notNull().default(sql`CURRENT_TIMESTAMP`),
});
