# استقرار روی Ubuntu VPS

پیش‌نیاز پیشنهادی: Ubuntu 24.04 یا 22.04، حداقل 1GB RAM و دسترسی SSH با sudo.

ساختار نهایی سرور:

- `/opt/task-adviser/app`: کد برنامه؛ قابل جایگزینی هنگام آپدیت
- `/opt/task-adviser/data`: دیتابیس، توکن‌ها، تنظیمات و گزارش‌ها؛ پایدار و محرمانه
- `/opt/task-adviser/venv`: محیط پایتون

فایل‌های محرمانه‌ای که باید در `/opt/task-adviser/data` قرار بگیرند:

- `.env` بر اساس `deploy/server.env.example`
- `credentials.json`
- `token.json`
- `task_manager.db` در صورت انتقال اطلاعات فعلی
