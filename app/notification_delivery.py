import logging


class TelegramFirstNotifications:
    """Use Telegram first; only a failed request may fall back to Bale."""

    def __init__(self, telegram, bale=None):
        self.telegram = telegram
        self.bale = bale

    async def _send(self, method, chat_id, *args, **kwargs):
        try:
            result = await getattr(self.telegram, method)(chat_id, *args, **kwargs)
            logging.info("Notification delivered: platform=telegram method=%s user=%s", method, chat_id)
            return result
        except Exception:
            if self.bale is None:
                raise
            logging.warning("Telegram notification failed; trying Bale: method=%s user=%s", method, chat_id, exc_info=True)
        result = await getattr(self.bale, method)(chat_id, *args, **kwargs)
        logging.info("Notification delivered: platform=bale fallback=true method=%s user=%s", method, chat_id)
        return result

    async def send_message(self, chat_id, *args, **kwargs):
        return await self._send("send_message", chat_id, *args, **kwargs)

    async def send_photo(self, chat_id, *args, **kwargs):
        return await self._send("send_photo", chat_id, *args, **kwargs)
