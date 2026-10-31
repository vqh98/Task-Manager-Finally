import unittest
from unittest.mock import AsyncMock, patch

from app.bot import start_polling_with_network_retry


class _FlakyDispatcher:
    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0

    async def start_polling(self, bot):
        self.calls += 1
        if self.calls <= self.failures:
            raise OSError("temporary DNS failure")


class PollingResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_transient_network_failure_until_polling_recovers(self):
        dispatcher = _FlakyDispatcher(failures=3)
        sleep = AsyncMock()

        with patch("app.bot.asyncio.sleep", sleep):
            await start_polling_with_network_retry(
                dispatcher, object(), initial_delay=2, max_delay=5,
            )

        self.assertEqual(dispatcher.calls, 4)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [2, 4, 5])


if __name__ == "__main__":
    unittest.main()
