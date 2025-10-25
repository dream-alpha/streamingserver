# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

import json
import struct
from pathlib import Path
from provider_manager import ProviderManager
from resolver_manager import ResolverManager
from recorder import Recorder
from debug import get_logger

logger = get_logger(__file__)


def send_message(sock, message):
    """Send a JSON message with length prefix (standard approach for large TCP data)"""
    json_data = json.dumps(message, ensure_ascii=False).encode('utf-8')
    length_prefix = struct.pack('>I', len(json_data))  # 4-byte big-endian length
    sock.sendall(length_prefix + json_data)


class RequestHandler():
    def __init__(self):
        self.provider_manager = ProviderManager()
        self.resolver_manager = ResolverManager()
        self.server = None  # Will be set when handler is attached to server
        self.request = None

    def handle_message(self, message):
        logger.info("message received: %s", message)
        cmd = message[0]
        args = message[1] if len(message) > 1 else {}
        logger.info("cmd: %s, args: %s", cmd, args)
        provider_id = args.get("provider", {}).get("provider_id", None)
        data_dir = args.get("data_dir", None)
        if not data_dir:
            data_dir = "/tmp/data"
        if provider_id:
            data_dir = Path(data_dir) / provider_id
            data_dir.mkdir(parents=True, exist_ok=True)
            logger.info("data_dir: %s", data_dir)

        match cmd:
            case "start":
                logger.info("Starting recording with args: %s", args)
                if self.server.recorder is None:
                    self.server.recorder = Recorder()
                    self.server.recorder.server = self.server

                # Get provider config to access provider-specific quality setting
                provider_config = None
                if provider_id:
                    providers_list = self.provider_manager.get_providers()
                    provider_config = next((p for p in providers_list if p.get("provider_id") == provider_id), None)

                # Use provider-specific quality only if client explicitly requests "provider"
                # Otherwise, allow client to override with specific quality (1080p, 720p, etc.)
                client_quality = args.get("quality", "provider")
                if provider_config and client_quality == "provider":
                    args["quality"] = provider_config.get("quality", "best")
                    logger.info("Using provider quality: %s", args["quality"])
                elif client_quality != "provider":
                    logger.info("Using client-specified quality: %s (overriding provider default)", client_quality)

                resolver_instance = self.resolver_manager.get_resolver(provider_id)

                if resolver_instance and hasattr(resolver_instance, 'resolve_url'):
                    logger.info("Resolving URL for recording: %s", args.get("url", ""))
                    resolve_result = resolver_instance.resolve_url(args)

                    # Check if resolver detected DRM protection
                    if resolve_result and resolve_result.get("drm_protected"):
                        logger.error("DRM protection detected in resolver: %s", resolve_result.get("drm_info", "Unknown DRM"))
                        # Send stop error message for DRM protection
                        stop_response = ["stop", {
                            "reason": "error",
                            "error_id": resolve_result.get("error_id", "drm_protected"),
                            "msg": resolve_result.get("error_msg", "DRM Protected Stream"),
                            "recorder": {"type": resolve_result.get("recorder_id", "unknown")}
                        }]
                        self.server.broadcast(stop_response)
                        # Continue to next command instead of closing connection
                        return

                    # Add metadata to resolve_result
                    if resolve_result and resolve_result.get("resolved"):
                        for key in args:
                            if key not in resolve_result:
                                resolve_result[key] = args[key]
                        resolve_result["socketserver"] = self.server
                        logger.info("Resolution successful: %s", resolve_result)

                        self.server.recorder.record_stream(resolve_result)
                    else:
                        # Resolution failed
                        logger.warning("URL resolution failed, stopping here.")

            case "stop":
                logger.info("Stopping recording - recorder state: %s",
                            "exists" if self.server.recorder is not None else "None")
                try:
                    if self.server.recorder is not None:
                        self.server.recorder.stop()
                except Exception as e:
                    logger.error("Error stopping recording: %s", e)
                    error_response = ["stop", {"status": "error", "error_id": "failure", "message": f"Failed to stop recording: {e}"}]
                    send_message(self.request, error_response)

            case "get_providers":
                try:
                    providers_data = self.provider_manager.get_providers()
                    response = ["get_providers", {"data": providers_data}]
                    logger.info("Sending providers: %s", response)
                    send_message(self.request, response)
                    logger.debug("Providers response sent successfully")
                except Exception as e:
                    logger.error("Error sending providers response: %s", e)
                    error_response = ["error", {"message": f"Failed to get providers: {e}"}]
                    send_message(self.request, error_response)

            case "get_categories":
                try:
                    provider_instance = None
                    if provider_id and data_dir:
                        provider_instance = self.provider_manager.get_provider(
                            provider_id,
                            data_dir
                        )

                    if provider_instance:
                        categories = provider_instance.get_categories()
                        response = ["get_categories", {"data": categories}]
                        send_message(self.request, response)
                        logger.debug("Categories response sent successfully")
                    else:
                        # Provider instance not available
                        logger.warning("Provider instance not available for command: %s", cmd)
                        error_response = ["error", {"message": f"Provider not available for {cmd}"}]
                        send_message(self.request, error_response)
                except Exception as e:
                    logger.error("Error getting categories: %s", e)
                    error_response = ["error", {"message": f"Failed to get categories: {e}"}]
                    send_message(self.request, error_response)

            case "get_media_items":
                try:
                    provider_instance = None
                    if provider_id and data_dir:
                        provider_instance = self.provider_manager.get_provider(
                            provider_id,
                            data_dir
                        )

                    if provider_instance:
                        category = args.get("category", None)
                        media_items = provider_instance.get_media_items(category)
                        response = ["get_media_items", {"data": media_items}]
                        logger.info("Sending %d media_items", len(media_items) if media_items else 0)

                        # Test JSON serialization first
                        response_json = json.dumps(response, ensure_ascii=False)
                        response_size = len(response_json.encode('utf-8'))
                        logger.info("Response size: %d bytes", response_size)

                        # Send response
                        send_message(self.request, response)
                        logger.info("Response sent successfully")
                    else:
                        # Provider instance not available
                        logger.warning("Provider instance not available for command: %s", cmd)
                        error_response = ["error", {"message": f"Provider not available for {cmd}"}]
                        send_message(self.request, error_response)
                except Exception as e:
                    logger.error("Error getting media items: %s", e)
                    error_response = ["error", {"message": f"Failed to get media items: {e}"}]
                    send_message(self.request, error_response)
            case _:
                logger.error("Unknown command: %s", cmd)
