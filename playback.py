"""Async playback clock; pause freezes elapsed distance and every exit clears."""
import asyncio
import routes


async def play(location, plan, commands, emit, interval=1.):
    loop = asyncio.get_running_loop()
    elapsed, paused, outcome = 0., False, 'finished'
    try:
        current = routes.frame(plan, elapsed)
        await asyncio.wait_for(location.set(current['latitude'], current['longitude']), 10)
        emit({"state": "running", **current})
        last = loop.time()
        while elapsed < plan['duration']:
            delay = interval if paused else min(interval, plan['duration']-elapsed)
            try:
                command = await asyncio.wait_for(commands.get(), delay)
            except TimeoutError:
                command = None
            now = loop.time()
            if command == 'stop':
                outcome = 'stopped'
                break
            if not paused:
                delta = now-last
                if delta > 5:
                    raise TimeoutError('playback clock stalled')
                elapsed = min(plan['duration'], elapsed+delta)
                current = routes.frame(plan, elapsed)
                await asyncio.wait_for(location.set(current['latitude'], current['longitude']), 10)
            if command == 'pause' and plan.get('mode') != 'fixed':
                paused = True
            elif command == 'resume':
                paused = False
            emit({"state": "paused" if paused else "running", **current})
            # Include device write latency in elapsed time, but never paused time.
            last = now if not paused else loop.time()
        return outcome
    finally:
        emit({"state": "stopping"})
        try:
            await asyncio.wait_for(location.clear(), 10)
            emit({"cleared": True})
        except Exception:
            emit({"cleared": False})
