"""One heavy native CLI probe at a time in the single-worker dashboard."""

import asyncio

SLOT = asyncio.Semaphore(1)
