"""One native DVT connection per replay; JSON events contain no device IDs."""
import asyncio
import json
from pathlib import Path
import signal
import sys
import threading
from typing import Annotated

import pymobiledevice3.common as common
common._HOMEFOLDER = Path(__file__).resolve().parent / 'runtime' / 'sdk-cache'

import typer
from typer_injector import InjectingTyper
from pymobiledevice3.cli.cli_common import ServiceProviderDep, async_command
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
from playback import play

cli = InjectingTyper(pretty_exceptions_enable=False)


def emit(data):
    print('TRACKLAB_EVENT ' + json.dumps(data), flush=True)


@cli.command()
@async_command
async def run(service_provider: ServiceProviderDep, plan_file: Annotated[Path, typer.Argument(exists=True)]):
    plan = json.loads(plan_file.read_text())
    commands = asyncio.Queue()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: loop.call_soon_threadsafe(commands.put_nowait, 'stop'))

    def listen():
        for line in sys.stdin:
            value = line.strip()
            if value in {'pause', 'resume', 'stop'}:
                loop.call_soon_threadsafe(commands.put_nowait, value)
        if not loop.is_closed():
            loop.call_soon_threadsafe(commands.put_nowait, 'stop')

    # Windows asyncio does not implement Unix pipe/signal methods. A daemon
    # stdin reader keeps the worker portable without changing global policies.
    threading.Thread(target=listen, daemon=True).start()
    async with DvtProvider(service_provider) as dvt, LocationSimulation(dvt) as location:
        outcome = await play(location, plan, commands, emit)
    emit({'state': outcome})


if __name__ == '__main__':
    try:
        cli()
    except Exception as error:
        emit({'state': 'failed', 'error': type(error).__name__})
        sys.exit(1)
