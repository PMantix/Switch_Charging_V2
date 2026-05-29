import sys
if sys.version_info < (3, 10):
    sys.exit("fleet dashboard requires Python 3.10+. "
             "Try: C:\\Users\\pmant\\AppData\\Local\\Python\\bin\\python.exe -m fleet")

"""
Fleet Dashboard — entry point.

    python -m fleet [--port 8080] [--no-cycle]

Starts the web dashboard and (unless --no-cycle) the AP cycler that
rotates through Pi APs to poll state and deliver commands.

--no-cycle is useful for development: the dashboard runs but no WiFi
switching happens. You can manually POST state via the API or let
the WebSocket show placeholder cards.
"""

import argparse
import logging
import sys

import uvicorn

from fleet.state_store import FleetStateStore
from fleet.web.app import create_app

log = logging.getLogger("fleet")


def main():
    parser = argparse.ArgumentParser(description="Switch Charging Fleet Dashboard")
    parser.add_argument("--port", type=int, default=8080, help="Web server port")
    parser.add_argument("--host", default="0.0.0.0", help="Web server bind address")
    parser.add_argument("--no-cycle", action="store_true",
                        help="Don't start the AP cycler (dashboard-only mode)")
    parser.add_argument("--poll-pause", type=float, default=2.0,
                        help="Seconds to pause between cycles when there was "
                             "work to do (joins/commands)")
    parser.add_argument("--idle-pause", type=float, default=30.0,
                        help="Seconds between rediscovery scans when every "
                             "visible Pi is already polled and idle. The "
                             "cycler wakes early when a command is queued.")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)-12s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    store = FleetStateStore()
    app = create_app(store)

    cycler = None
    if not args.no_cycle:
        from fleet.ap_cycler import APCycler
        cycler = APCycler(
            store=store,
            poll_pause=args.poll_pause,
            idle_pause=args.idle_pause,
        )

        def cycler_info():
            return {
                "running": True,
                "cycle_count": cycler.cycle_count,
                "current_pi": cycler.current_pi,
                "phase": cycler.current_phase,
            }
        app.state.cycler = cycler
        app.state.cycler_info = cycler_info
        cycler.start()
        log.info("AP cycler started (auto-discovery from WiFi scan)")
    else:
        log.info("Running in dashboard-only mode (no AP cycling)")

    log.info("Fleet dashboard: http://%s:%d", args.host, args.port)

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())
    except KeyboardInterrupt:
        pass
    finally:
        if cycler:
            cycler.stop()


if __name__ == "__main__":
    main()
