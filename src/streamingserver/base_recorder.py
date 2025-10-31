# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

import os
import glob
import threading
import subprocess
from debug import get_logger

logger = get_logger(__file__)


class BaseRecorder:
    """Base class for all recorders"""

    def __init__(self, name: str = None, socketserver=None):
        """Initialize the base recorder"""
        self.name = name or self.__class__.__name__
        self.socketserver = socketserver
        self.is_running = False
        self.thread = None
        self.stop_event = threading.Event()
        self.stop_sent = False  # Flag to prevent duplicate stop messages
        logger.info(f"Initialized {self.name}")

    def start_thread(self, resolve_result) -> bool:
        """Start recording in background thread"""
        self.socketserver = resolve_result["socketserver"]

        if self.is_running:
            return False

        self.stop_event.clear()
        # Use wrapper method as thread target to ensure proper cleanup
        self.thread = threading.Thread(target=self._thread_wrapper, args=(resolve_result,), daemon=True)
        self.thread.start()
        self.is_running = True
        logger.info(f"Started {self.name}")
        return True

    def _thread_wrapper(self, resolve_result):
        """Thread wrapper that ensures cleanup when thread ends"""
        try:
            # Call the child class's record_start method
            self.record_start(resolve_result)
        except Exception as e:
            logger.error(f"Error in {self.name} thread: {e}")
            self.on_thread_error(e)
        finally:
            # Always clean up when thread ends
            self.is_running = False
            logger.info(f"{self.name} thread ended, cleaned up is_running flag")
            self.on_thread_ended()

    def record_start(self, resolve_result):
        """
        Base recording setup - provides common cleanup.
        Child classes should override this method and call super().record_start(resolve_result)
        to get the common cleanup, then implement their specific recording logic.
        """
        # Clean up old files (handle test case where resolve_result might be None)
        if resolve_result:
            pattern = os.path.join(resolve_result.get("rec_dir", "/tmp"), "stream*")
            subprocess.run(["rm", "-f"] + glob.glob(pattern), check=False)
            logger.info(f"Cleaned up old files matching: {pattern}")

    def on_thread_ended(self):
        """Called when the recording thread ends (override in child classes if needed)"""
        self.is_running = False  # Ensure flag is cleared
        logger.info(f"{self.name} has fully stopped")

    def on_thread_error(self, error: Exception, error_id: str = "failure", recorder_id: str = "unknown"):
        """Called when the recording thread encounters an error (override in child classes if needed)"""
        self.is_running = False  # Ensure flag is cleared
        logger.error(f"{self.name} encountered an error: {error}")

        # Send stop message to client if not already sent
        if self.socketserver and not self.stop_sent:
            logger.info(f"Broadcasting stop message from {self.name} due to error")
            self.socketserver.broadcast(["stop", {"reason": "error", "error_id": error_id, "msg": str(error), "recorder_id": recorder_id}])
            self.stop_sent = True

    def stop(self) -> bool:
        """Stop recording and wait for thread to finish"""
        if not self.is_running:
            return True

        # Set stop_sent to prevent any completion messages from on_thread_ended
        self.stop_sent = True

        logger.info(f"Sending stop signal to {self.name}...")
        self.stop_event.set()
        if self.thread:
            logger.info(f"Waiting for {self.name} thread to finish...")
            self.thread.join(timeout=5)  # This blocks until thread is fully done
            logger.info(f"{self.name} thread has finished")
        self.is_running = False
        logger.info(f"{self.name} fully stopped")
        return True

    def start_playback(self, video_url: str, output_file: str, recorder: str):
        """
        Function called by timer to start playback
        This can be used to signal that recording has started and playback can begin
        """
        logger.info("Playback has started")

        # Notify client that playbook can start
        if self.socketserver:
            self.socketserver.broadcast(["start", {
                "url": video_url,
                "rec_file": output_file,
                "section_index": 0,
                "segment_index": 0,
                "recorder_id": recorder
            }])
