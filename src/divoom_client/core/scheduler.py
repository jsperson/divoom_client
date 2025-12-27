"""Scheduler for periodic data updates and display refresh."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from divoom_client.datasources.manager import DataSourceManager

logger = logging.getLogger(__name__)


class Scheduler:
    """Manages periodic data fetching and display updates."""

    def __init__(self):
        """Initialize the scheduler."""
        self._scheduler = AsyncIOScheduler()
        self._data_manager: Optional[DataSourceManager] = None
        self._on_data_update: Optional[Callable[[dict[str, Any]], None]] = None
        self._running = False

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._running

    def set_data_manager(self, manager: DataSourceManager) -> None:
        """Set the data source manager.

        Args:
            manager: DataSourceManager instance
        """
        self._data_manager = manager

    def set_update_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Set callback to be invoked when data is updated.

        Args:
            callback: Function that receives updated data dict
        """
        self._on_data_update = callback

    def _schedule_data_sources(self) -> None:
        """Schedule refresh jobs for each data source."""
        if not self._data_manager:
            return

        for name, source in self._data_manager.sources.items():
            if not source.config.enabled:
                continue

            interval = source.config.refresh_seconds

            # Create async job for this source
            async def refresh_source(source_name: str = name) -> None:
                try:
                    await self._data_manager.refresh(source_name)
                    logger.debug(f"Refreshed data source: {source_name}")

                    # Trigger update callback with all data
                    if self._on_data_update:
                        data = self._data_manager.get_data_context()
                        self._on_data_update(data)

                except Exception as e:
                    logger.error(f"Failed to refresh {source_name}: {e}")

            self._scheduler.add_job(
                refresh_source,
                trigger=IntervalTrigger(seconds=interval),
                id=f"datasource_{name}",
                name=f"Refresh {name}",
                replace_existing=True,
            )

            logger.info(f"Scheduled '{name}' to refresh every {interval}s")

    def add_job(
        self,
        func: Callable,
        interval_seconds: int,
        job_id: str,
        name: Optional[str] = None,
    ) -> None:
        """Add a custom scheduled job.

        Args:
            func: Function to call (can be async)
            interval_seconds: Interval between calls
            job_id: Unique job identifier
            name: Human-readable job name
        """
        self._scheduler.add_job(
            func,
            trigger=IntervalTrigger(seconds=interval_seconds),
            id=job_id,
            name=name or job_id,
            replace_existing=True,
        )
        logger.info(f"Added job '{job_id}' with {interval_seconds}s interval")

    def remove_job(self, job_id: str) -> None:
        """Remove a scheduled job.

        Args:
            job_id: Job identifier to remove
        """
        try:
            self._scheduler.remove_job(job_id)
            logger.info(f"Removed job '{job_id}'")
        except Exception as e:
            logger.warning(f"Could not remove job '{job_id}': {e}")

    async def start(self) -> None:
        """Start the scheduler."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        # Schedule data source refreshes
        self._schedule_data_sources()

        # Start the scheduler
        self._scheduler.start()
        self._running = True
        logger.info("Scheduler started")

        # Do initial fetch of all data sources
        if self._data_manager and self._data_manager.sources:
            logger.info("Performing initial data fetch...")
            try:
                data = await self._data_manager.refresh_all()
                if self._on_data_update:
                    self._on_data_update(data)
            except Exception as e:
                logger.error(f"Initial data fetch failed: {e}")

    def stop(self) -> None:
        """Stop the scheduler."""
        if not self._running:
            return

        self._scheduler.shutdown(wait=False)
        self._running = False
        logger.info("Scheduler stopped")

    def get_jobs(self) -> list[dict[str, Any]]:
        """Get information about scheduled jobs.

        Returns:
            List of job info dictionaries
        """
        jobs = []
        for job in self._scheduler.get_jobs():
            next_run = job.next_run_time
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": next_run.isoformat() if next_run else None,
            })
        return jobs
