import os
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import perf_counter, sleep
from typing import Any

from loguru import logger

from src.connection import generate_server_connections
from src.functions import configure_logger
from src.settings import AppSettings, load_settings
from src.users import generate_all_server_users
from src.sync_inventory import fetch_watched_inventory, generate_sync_inventory
from src.sync_plan import generate_watched_plan


WatchedPlan = dict[Any, dict[str, list[Any]]]


@dataclass
class RunState:
    """Outcome of the most recent pass, readable by other components."""

    running: bool = False
    last_started: datetime | None = None
    last_duration: float | None = None
    last_error: str | None = None
    last_planned_servers: int = 0
    durations: list[float] = field(default_factory=list)

    @property
    def average_time(self) -> float:
        if not self.durations:
            return 100.0  # Seed average time at 100
        return sum(self.durations) / len(self.durations)


def build_plan(settings: AppSettings, average_time: float) -> WatchedPlan:
    """Read every server and compute the watched updates, without writing."""
    logger.info(f"Dryrun: {settings.dryrun}")

    logger.bind(data=settings).debug("Settings")
    if settings.debug_level != "DEBUG":
        # Avoid printing both full settings and these individual when in DEBUG mode
        logger.bind(data=settings.user_mappings).info("User Mapping: ")
        logger.bind(data=settings.whitelist_users).info("Whitelist Users: ")
        logger.bind(data=settings.blacklist_users).info("Blacklist Users: ")
        logger.bind(data=settings.library_mappings).info("Library Mapping: ")
        logger.bind(data=settings.whitelist_libraries).info("Whitelist Libraries: ")
        logger.bind(data=settings.blacklist_libraries).info("Blacklist Libraries: ")
        logger.bind(data=settings.whitelist_library_types).info(
            "Whitelist Library Types: "
        )
        logger.bind(data=settings.blacklist_library_types).info(
            "Blacklist Library Types: "
        )

    servers = generate_server_connections(settings)

    # Generate lists of users participating in outgoing or incoming syncs.
    server_users = generate_all_server_users(servers, settings)
    logger.debug("Selected users for {} servers", len(server_users))

    # Discover accessible libraries once per user, then filter using cached inventories.
    server_user_libraries = generate_sync_inventory(server_users, settings)
    logger.debug("Selected user libraries for {} servers", len(server_user_libraries))

    servers_watched = fetch_watched_inventory(server_user_libraries)
    logger.debug("Fetched watched data for {} servers", len(servers_watched))
    logger.bind(
        data={
            server.server_settings.name: watched
            for server, watched in servers_watched.items()
        }
    ).trace("Fetched watched history")

    watched_plan = generate_watched_plan(servers_watched, settings, average_time)
    logger.debug("Planned watched updates for {} servers", len(watched_plan))
    logger.bind(
        data={
            server.server_settings.name: batches
            for server, batches in watched_plan.items()
        }
    ).trace("Planned watched updates")

    return watched_plan


def apply_plan(watched_plan: WatchedPlan) -> None:
    """Write a plan produced by build_plan to its destination servers."""
    for destination, source_batches in watched_plan.items():
        logger.info("Applying watched plan to {}", destination.info())
        for source_name, updates in source_batches.items():
            # For relayed winners this is the final authorized hop; each
            # update's relay_path retains the original source and full route.
            destination.update_watched(updates, source_name)


def main_loop(settings: AppSettings, average_time: float) -> None:
    apply_plan(build_plan(settings, average_time))


def run_pass(settings: AppSettings, state: RunState) -> None:
    """Run one full pass, recording its outcome on state."""
    state.running = True
    state.last_started = datetime.now(timezone.utc)
    state.last_error = None
    start = perf_counter()
    try:
        plan = build_plan(settings, state.average_time)
        state.last_planned_servers = len(plan)
        apply_plan(plan)
        state.last_duration = perf_counter() - start
        state.durations.append(state.last_duration)
    except Exception as error:
        state.last_duration = perf_counter() - start
        state.last_error = str(error)
        raise
    finally:
        state.running = False


def main() -> None:
    # load_settings resolves ENV_FILE / YAML_FILE and creates one startup
    # snapshot for the lifetime of the process.
    settings: AppSettings = load_settings()

    state = RunState()
    controller = None
    if settings.gui_enabled:
        from src.web import Controller, start_web_server

        controller = Controller(settings, state=state)
        start_web_server(controller)

    def wait_for_next_run() -> None:
        """Sleep until the next pass, waking early when the GUI requests a run."""
        if controller is None:
            sleep(settings.sleep_duration)
            return
        controller.next_run = datetime.now(timezone.utc) + timedelta(
            seconds=settings.sleep_duration
        )
        controller.trigger.wait(settings.sleep_duration)
        controller.trigger.clear()
        controller.next_run = None

    while True:
        try:
            # Reconfigure the logger on each loop so the logs are rotated on each run
            configure_logger(settings.log_file, settings.debug_level)
            if controller is None:
                run_pass(settings, state)
            else:
                controller.attach_log_sink()
                with controller.run_lock:
                    run_pass(settings, state)

            logger.info(f"Average time: {state.average_time}")

            if settings.run_only_once:
                break

            logger.info(f"Looping in {settings.sleep_duration}")
            wait_for_next_run()

        except Exception as error:
            if isinstance(error, list):
                for message in error:
                    logger.error(message)
            else:
                logger.error(error)

            logger.error(traceback.format_exc())

            if settings.run_only_once:
                break

            logger.info(f"Retrying in {settings.sleep_duration}")
            wait_for_next_run()

        except KeyboardInterrupt:
            if state.durations:
                logger.info(f"Average time: {state.average_time}")
            logger.info("Exiting")
            os._exit(0)
