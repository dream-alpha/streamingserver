#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Navigator - Socket Client for Testing Provider Commands

This script provides an interactive command-line interface for testing
the socket server's provider functionality. It allows users to:
1. Get available providers
2. Select a provider and get its categories
3. Select a category and get its media items
4. Record selected media items
"""

import json
import queue
import socket
import struct
import sys
import argparse
import os
import subprocess
import threading
import time
from pathlib import Path


def send_message(socket_conn, message):
    """Send a JSON message with length prefix (standard approach for large TCP data)"""
    json_data = json.dumps(message, ensure_ascii=False).encode('utf-8')
    length_prefix = struct.pack('>I', len(json_data))  # 4-byte big-endian length
    socket_conn.sendall(length_prefix + json_data)


def recv_length_prefixed_message(socket_conn):
    """Receive a length-prefixed JSON message"""
    # First, receive the 4-byte length prefix
    length_data = b''
    while len(length_data) < 4:
        chunk = socket_conn.recv(4 - len(length_data))
        if not chunk:
            raise ConnectionError("Connection closed while reading length prefix")
        length_data += chunk

    # Unpack the length
    message_length = struct.unpack('>I', length_data)[0]

    # Now receive the exact message
    json_data = b''
    while len(json_data) < message_length:
        chunk = socket_conn.recv(message_length - len(json_data))
        if not chunk:
            raise ConnectionError("Connection closed while reading message")
        json_data += chunk

    # Parse and return the JSON
    return json.loads(json_data.decode('utf-8'))


class Navigator:
    def __init__(self, host="localhost", port=5000):
        self.host = host
        self.port = port
        self.socket = None
        self.providers = []
        self.selected_provider = None
        self.categories = []
        self.selected_category = None
        self.media_items = []
        self.selected_media_item = None

    def connect(self):
        """Connect to the socket server."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(10)  # Add 10-second timeout
            print(f"Attempting to connect to {self.host}:{self.port}...")
            self.socket.connect((self.host, self.port))

            # Read the initial 'ready' message with timeout
            self.socket.settimeout(5)  # 5-second timeout for receiving
            ready_msg = recv_length_prefixed_message(self.socket)
            self.socket.settimeout(None)  # Remove timeout for normal operation

            print(f"Server says: {ready_msg}")
            print(f"Connected to server at {self.host}:{self.port}")

            # Small delay to ensure connection is fully established
            time.sleep(0.1)
            return True
        except Exception as e:
            print(f"Failed to connect: {e}")
            return False

    def send_command(self, command, args=None):
        """Send a command to the server and return the response."""
        if args is None:
            args = {}

        message = [command, args]
        try:
            send_message(self.socket, message)

            # For commands that expect a response
            if command in {"get_providers", "get_categories", "get_media_items"}:
                return self._receive_response(command)
            return None
        except Exception as e:
            print(f"Error sending command: {e}")
            return None

    def _handle_async_message(self, message):
        """Handle unsolicited messages from the server."""
        print(f"🔍 Debug: _handle_async_message called with: {message}")  # Debug info

        if isinstance(message, list) and len(message) >= 2:
            msg_type = message[0]
            msg_data = message[1] if len(message) > 1 else {}

            print(f"🔍 Debug: Processing message type: {msg_type}")  # Debug info

            if msg_type == "start":
                print("\n🎉 📡 Server: Recording started!")
                print("🎬 Playback can now begin!")
                if isinstance(msg_data, dict):
                    if "url" in msg_data:
                        url_display = msg_data['url'][:60] + "..." if len(msg_data['url']) > 60 else msg_data['url']
                        print(f"   📹 Stream: {url_display}")
                    if "rec_file" in msg_data:
                        print(f"   💾 Output: {msg_data['rec_file']}")
                    if "section_index" in msg_data and "segment_index" in msg_data:
                        print(f"   📊 Position: Section {msg_data['section_index']}, Segment {msg_data['segment_index']}")
            elif msg_type == "stop":
                print("\n⏹️ 📡 Server: Recording stopped!")
                if isinstance(msg_data, dict) and "reason" in msg_data:
                    reason_emoji = "✅" if msg_data['reason'] == "complete" else "❌" if msg_data['reason'] == "error" else "ℹ️"
                    print(f"   {reason_emoji} Reason: {msg_data['reason']}")
            else:
                print(f"\n📡 Server notification: {msg_type}")
        else:
            print(f"🔍 Debug: Invalid message format: {message}")

    def _receive_response(self, command):
        """Receive and parse a response from the server using length-prefixed messages."""
        try:
            self.socket.settimeout(60)  # Timeout for large responses

            while True:
                response = recv_length_prefixed_message(self.socket)

                # Check for error responses
                if (isinstance(response, list) and len(response) >= 2
                        and response[0] == "error"):
                    print(f"Server error: {response[1].get('message', 'Unknown error')}")
                    return None

                # Handle async messages (start, stop notifications)
                if (isinstance(response, list) and len(response) >= 1
                        and response[0] in {"start", "stop"}
                        and response[0] != command):
                    self._handle_async_message(response)
                    continue  # Keep looking for the expected response

                # Check if this is the expected response type
                if (isinstance(response, list) and len(response) >= 1
                        and response[0] == command):
                    return response

                # If we got a different response type, continue waiting
                continue

        except Exception as e:
            print(f"Error receiving response for {command}: {e}")
            return None
        finally:
            self.socket.settimeout(None)

    def get_providers(self):
        """Get list of available providers."""
        print("\n" + "=" * 50)
        print("GETTING PROVIDERS")
        print("=" * 50)

        max_retries = 2
        for attempt in range(max_retries):
            response = self.send_command("get_providers")

            if response and isinstance(response, list) and len(response) >= 2 and response[0] == "get_providers":
                self.providers = response[1]["data"]
                print(f"Found {len(self.providers)} providers:")
                for i, provider in enumerate(self.providers):
                    print(f"{i + 1:2d}. {provider.get('name', 'Unknown')} (ID: {provider.get('provider_id', 'N/A')})")
                return True

            if attempt < max_retries - 1:
                print("Retrying...")
                time.sleep(0.3)  # Brief delay before retry

        print("Failed to get providers")
        return False

    def select_provider(self):
        """Get available providers and allow user to select one."""
        # Automatically get providers if not available
        if not self.providers:
            if not self.get_providers():
                return False

        while True:
            try:
                choice = input(f"\nSelect provider (1-{len(self.providers)}) or 'q' to quit: ").strip()  # pylint: disable=bad-builtin
                if choice.lower() == 'q':
                    return False

                index = int(choice) - 1
                if 0 <= index < len(self.providers):
                    self.selected_provider = self.providers[index]
                    print(f"Selected provider: {self.selected_provider.get('name', 'Unknown')}")
                    return True
                print(f"Please enter a number between 1 and {len(self.providers)}")
            except ValueError:
                print("Please enter a valid number or 'q'")

    def get_categories(self):
        """Get categories for the selected provider."""
        if not self.selected_provider:
            print("No provider selected")
            return False

        print("\n" + "=" * 50)
        print(f"GETTING CATEGORIES FOR: {self.selected_provider.get('name', 'Unknown')}")
        print("=" * 50)

        args = {
            "provider": self.selected_provider,
            "data_dir": "/home/alpha/streamingserver"
        }

        max_retries = 2
        for attempt in range(max_retries):
            response = self.send_command("get_categories", args)
            if response and response[0] == "get_categories":
                self.categories = response[1]["data"]
                print(f"Found {len(self.categories)} categories:")
                for i, category in enumerate(self.categories):
                    # Categories might be strings or objects
                    if isinstance(category, str):
                        print(f"{i + 1:2d}. {category}")
                    else:
                        print(f"{i + 1:2d}. {category.get('name', category)}")
                return True

            if attempt < max_retries - 1:
                print("Retrying...")
                time.sleep(0.3)

        print("Failed to get categories")
        return False

    def select_category(self):
        """Get categories for the selected provider and allow user to select one."""
        # Ensure we have a selected provider first
        if not self.selected_provider:
            if not self.select_provider():
                return False

        # Automatically get categories if not available
        if not self.categories:
            if not self.get_categories():
                return False

        while True:
            try:
                choice = input(f"\nSelect category (1-{len(self.categories)}) or 'q' to quit: ").strip()  # pylint: disable=bad-builtin
                if choice.lower() == 'q':
                    return False

                index = int(choice) - 1
                if 0 <= index < len(self.categories):
                    self.selected_category = self.categories[index]
                    category_name = self.selected_category if isinstance(self.selected_category, str) else self.selected_category.get('name', 'Unknown')
                    print(f"Selected category: {category_name}")
                    return True
                print(f"Please enter a number between 1 and {len(self.categories)}")
            except ValueError:
                print("Please enter a valid number or 'q'")

    def get_media_items(self):
        """Get media items for the selected category."""
        if not self.selected_provider or not self.selected_category:
            print("Need both provider and category selected")
            return False

        category_name = self.selected_category if isinstance(self.selected_category, str) else self.selected_category.get('name', 'Unknown')
        print("\n" + "=" * 50)
        print("GETTING MEDIA ITEMS")
        print(f"Provider: {self.selected_provider.get('name', 'Unknown')}")
        print(f"Category: {category_name}")
        print("=" * 50)

        args = {
            "provider": self.selected_provider,
            "category": self.selected_category,
            "data_dir": str(Path.home() / "streamingserver_data")
        }

        max_retries = 2
        for attempt in range(max_retries):
            response = self.send_command("get_media_items", args)
            if response and response[0] == "get_media_items":
                self.media_items = response[1]["data"]
                print(f"Found {len(self.media_items)} media items:")
                for i, item in enumerate(self.media_items[:20]):  # Show max 20 items
                    if isinstance(item, dict):
                        title = item.get('title', item.get('name', 'Unknown'))
                        url = item.get('url', 'N/A')
                        print(f"{i + 1:2d}. {title}")
                        print(f"     URL: {url}")
                    else:
                        print(f"{i + 1:2d}. {item}")

                if len(self.media_items) > 20:
                    print(f"... and {len(self.media_items) - 20} more items")
                return True

            if attempt < max_retries - 1:
                print("Retrying...")
                time.sleep(0.5)  # Shorter delay for media items

        print("Failed to get media items")
        return False

    def select_media_item(self):
        """Get media items for the selected category and allow user to select one."""
        # Ensure we have a selected provider and category first
        if not self.selected_provider:
            if not self.select_provider():
                return False

        if not self.selected_category:
            if not self.select_category():
                return False

        # Automatically get media items if not available
        if not self.media_items:
            if not self.get_media_items():
                return False

        print("\n" + "=" * 50)
        print("SELECT MEDIA ITEM")
        print("=" * 50)

        # Show media items for selection
        for i, item in enumerate(self.media_items[:20]):  # Show max 20 items
            if isinstance(item, dict):
                title = item.get('title', item.get('name', 'Unknown'))
                print(f"{i + 1:2d}. {title}")
            else:
                print(f"{i + 1:2d}. {item}")

        if len(self.media_items) > 20:
            print(f"... and {len(self.media_items) - 20} more items (only first 20 selectable)")

        while True:
            try:
                choice = input(f"\nSelect media item (1-{min(20, len(self.media_items))}) or 'q' to quit: ").strip()  # pylint: disable=bad-builtin
                if choice.lower() == 'q':
                    return False

                index = int(choice) - 1
                if 0 <= index < min(20, len(self.media_items)):
                    self.selected_media_item = self.media_items[index]
                    if isinstance(self.selected_media_item, dict):
                        title = self.selected_media_item.get('title', self.selected_media_item.get('name', 'Unknown'))
                    else:
                        title = str(self.selected_media_item)
                    print(f"Selected media item: {title}")
                    return True
                print(f"Please enter a number between 1 and {min(20, len(self.media_items))}")
            except ValueError:
                print("Please enter a valid number or 'q'")

    def record_media_item(self):
        """Start recording the selected media item.
        If no item is selected, automatically select the first provider,
        first category, and first media item."""
        if not self.selected_media_item:
            print("No media item selected. Automatically selecting first provider, category, and media item...")

            # Get providers if needed
            if not self.providers:
                if not self.get_providers():
                    print("Failed to get providers.")
                    return False

            # Select first provider if not selected
            if not self.selected_provider and self.providers:
                self.selected_provider = self.providers[0]
                provider_name = self.selected_provider.get('name', 'Unknown')
                print(f"Selected first provider: {provider_name}")

            # Get categories if needed
            if not self.categories and self.selected_provider:
                if not self.get_categories():
                    print("Failed to get categories.")
                    return False

            # Select first category if not selected
            if not self.selected_category and self.categories:
                self.selected_category = self.categories[0]
                category_name = self.selected_category if isinstance(self.selected_category, str) else self.selected_category.get('name', 'Unknown')
                print(f"Selected first category: {category_name}")

            # Get media items if needed
            if not self.media_items and self.selected_provider and self.selected_category:
                if not self.get_media_items():
                    print("Failed to get media items.")
                    return False

            # Select first media item if not selected
            if not self.selected_media_item and self.media_items:
                self.selected_media_item = self.media_items[0]
                if isinstance(self.selected_media_item, dict):
                    title = self.selected_media_item.get('title', self.selected_media_item.get('name', 'Unknown'))
                else:
                    title = str(self.selected_media_item)
                print(f"Selected first media item: {title}")

            # If still no media item, return failure
            if not self.selected_media_item:
                print("Failed to automatically select a media item. Please make selections manually.")
                return False

        return self.start_recording(self.selected_media_item)

    def select_and_record_media_item(self):
        """Allow user to select a media item and start recording it."""
        if not self.media_items:
            print("No media items available. Get media items first.")
            return False

        print("\n" + "=" * 50)
        print("SELECT MEDIA ITEM TO RECORD")
        print("=" * 50)

        # Show media items for selection
        for i, item in enumerate(self.media_items[:20]):  # Show max 20 items
            if isinstance(item, dict):
                title = item.get('title', item.get('name', 'Unknown'))
                print(f"{i + 1:2d}. {title}")
            else:
                print(f"{i + 1:2d}. {item}")

        if len(self.media_items) > 20:
            print(f"... and {len(self.media_items) - 20} more items (only first 20 selectable)")

        while True:
            try:
                choice = input(f"\nSelect media item (1-{min(20, len(self.media_items))}) or 'q' to quit: ").strip()  # pylint: disable=bad-builtin
                if choice.lower() == 'q':
                    return False

                index = int(choice) - 1
                if 0 <= index < min(20, len(self.media_items)):
                    selected_item = self.media_items[index]
                    return self.start_recording(selected_item)
                print(f"Please enter a number between 1 and {min(20, len(self.media_items))}")
            except ValueError:
                print("Please enter a valid number or 'q'")

    def start_recording(self, media_item):
        """Start recording the selected media item."""
        if isinstance(media_item, dict):
            title = media_item.get('title', media_item.get('name', 'Unknown'))
            url = media_item.get('url', '')
        else:
            title = str(media_item)
            url = str(media_item)

        print(f"\nStarting recording for: {title}")
        print(f"URL: {url}")

        # Get recording directory
        rec_dir = input("Enter recording directory (default: /tmp): ").strip()  # pylint: disable=bad-builtin
        if not rec_dir:
            rec_dir = "/tmp"

        # Get buffering setting
        buffering_input = input("Enter buffering segments (default: 5): ").strip()  # pylint: disable=bad-builtin
        try:
            buffering = int(buffering_input) if buffering_input else 5
        except ValueError:
            buffering = 5

        # Prepare start command
        args = {
            "url": url,
            "rec_dir": rec_dir,
            "show_ads": False,
            "buffering": buffering,
            "av1": True,
            "quality": "best"
        }

        # Add provider information if we have a selected provider
        if self.selected_provider:
            args["provider"] = {"provider_id": self.selected_provider["provider_id"]}
            # Add data_dir if available (use a default for testing)
            args["data_dir"] = str(Path.home() / "streamingserver_data")

        print("\nSending start command...")
        print(f"Recording to: {rec_dir}")
        print(f"Buffering: {buffering} segments")

        # Send start command (no response expected)
        self.send_command("start", args)

        print("📡 Start command sent to server...")
        print("🎬 Recording should begin shortly - watch for server notifications!")
        print("📋 Use menu option 10 to stop recording when done.")

        # Check for immediate server response multiple times
        print("🔍 Checking for server notifications...")
        for i in range(5):  # Check 5 times over 2.5 seconds
            time.sleep(0.5)
            print(f"🔍 Check {i + 1} / 5...")
            self.check_for_async_messages()

        print("✅ Initial notification check complete.")
        return True

    def stop_recording(self):
        """Stop the current recording."""
        print("\n⏹️  Sending stop command...")

        # Send stop command without expecting a response (like start command)
        try:
            message = ["stop", {}]
            self.socket.sendall((json.dumps(message) + '\n').encode())
            print("📡 Stop command sent to server!")
            print("🔍 Watch for server stop notifications...")

            # Check for immediate async response
            time.sleep(0.5)
            self.check_for_async_messages()

        except Exception as e:
            print(f"❌ Error sending stop command: {e}")
            return False

        return True

    def check_for_async_messages(self):
        """Check for any pending async messages from server without blocking."""
        if not self.socket:
            return

        original_timeout = None
        try:
            # Set socket to non-blocking mode temporarily
            original_timeout = self.socket.gettimeout()
            self.socket.settimeout(0.01)  # Very short timeout for non-blocking check

            messages_received = 0
            while messages_received < 10:  # Limit to prevent infinite loop
                try:
                    data = self.socket.recv(4096)
                    if not data:
                        break

                    # Process any complete messages
                    for line in data.decode('utf-8', errors='ignore').split('\n'):
                        line = line.strip()
                        if line:
                            try:
                                message = json.loads(line)
                                if isinstance(message, list) and len(message) >= 1:
                                    if message[0] in {"start", "stop"}:
                                        self._handle_async_message(message)
                                        messages_received += 1
                                        print(f"🔄 Processed async message: {message[0]}")  # Debug info
                            except json.JSONDecodeError:
                                continue

                except socket.timeout:
                    # No more messages available
                    break
                except Exception as e:
                    print(f"🔍 Debug: Exception in async check: {e}")
                    break

        except Exception as e:
            print(f"🔍 Debug: Error in check_for_async_messages: {e}")
        finally:
            # Restore original timeout
            if original_timeout is not None:
                self.socket.settimeout(original_timeout)

    def main_menu(self):
        """Display the main menu and handle user choices."""
        while True:
            # Check for any async messages before showing menu
            self.check_for_async_messages()

            print("\n" + "=" * 50)
            print("NAVIGATOR - Provider Testing Menu")
            print("=" * 50)
            print("1. Select Provider (auto-gets providers)")
            print("2. Select Category (auto-gets categories)")
            print("3. Select Media Item (auto-gets media items)")
            print("4. Show Current Selection")
            print("5. Reset Selection")
            print("6. Record Selected Media Item")
            print("7. Stop Recording")
            print("q. Quit")

            print("\nEnter your choice: ", end='', flush=True)

            # Check for async messages while waiting for input

            input_queue = queue.Queue()

            def get_input():
                try:
                    user_input = input()  # pylint: disable=bad-builtin
                    input_queue.put(user_input)
                except (EOFError, KeyboardInterrupt):
                    input_queue.put("")

            input_thread = threading.Thread(target=get_input, daemon=True)
            input_thread.start()

            # Wait for input while checking for async messages
            choice = ""
            while input_thread.is_alive():
                try:
                    choice = input_queue.get(timeout=0.2)
                    break
                except queue.Empty:
                    self.check_for_async_messages()
                    continue

            choice = choice.strip().lower()

            if choice == '1':
                self.select_provider()
            elif choice == '2':
                self.select_category()
            elif choice == '3':
                self.select_media_item()
            elif choice == '4':
                self.show_current_selection()
            elif choice == '5':
                self.reset_selection()
            elif choice == '6':
                self.record_media_item()
            elif choice == '7':
                self.stop_recording()
            elif choice == 'q':
                break
            else:
                print("Invalid choice. Please try again.")

    def show_current_selection(self):
        """Show the current selection status."""
        print("\n" + "-" * 30)
        print("CURRENT SELECTION:")
        print("-" * 30)
        if self.selected_provider:
            print(f"Provider: {self.selected_provider.get('name', 'Unknown')}")
        else:
            print("Provider: None")

        if self.selected_category:
            category_name = self.selected_category if isinstance(self.selected_category, str) else self.selected_category.get('name', 'Unknown')
            print(f"Category: {category_name}")
        else:
            print("Category: None")

        if self.selected_media_item:
            if isinstance(self.selected_media_item, dict):
                title = self.selected_media_item.get('title', self.selected_media_item.get('name', 'Unknown'))
            else:
                title = str(self.selected_media_item)
            print(f"Media Item: {title}")
        else:
            print("Media Item: None")

        print(f"Available providers: {len(self.providers)}")
        print(f"Available categories: {len(self.categories)}")
        print(f"Available media items: {len(self.media_items)}")

    def reset_selection(self):
        """Reset all selections."""
        self.selected_provider = None
        self.selected_category = None
        self.selected_media_item = None
        self.providers = []
        self.categories = []
        self.media_items = []
        print("Selection reset")

    def disconnect(self):
        """Disconnect from the server."""
        if self.socket:
            self.socket.close()
            print("Disconnected from server")

    def run(self):
        """Run the navigator application."""
        print("Navigator - Socket Client for Provider Testing")
        print("=" * 50)

        if self.connect():
            try:
                self.main_menu()
            except KeyboardInterrupt:
                print("\n\nInterrupted by user")
            finally:
                self.disconnect()
        else:
            sys.exit(1)


def main():
    """Main entry point."""
    # Kill any previously running navigator instances
    try:
        # Get current process ID to avoid killing ourselves
        current_pid = os.getpid()
        print(f"Current navigator instance PID: {current_pid}")

        # Find and kill other navigator.py processes
        cmd = ["pgrep", "-f", "python.*navigator.py"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            for pid in result.stdout.strip().split('\n'):
                if pid and int(pid) != current_pid:
                    print(f"Killing previous navigator instance with PID: {pid}")
                    try:
                        subprocess.run(["kill", "-9", pid], check=False)
                    except Exception as e:
                        print(f"Error killing process {pid}: {e}")

        print("Starting new navigator instance...")
    except Exception as e:
        print(f"Error handling previous instances: {e}")

    parser = argparse.ArgumentParser(description="Navigator - Socket Client for Provider Testing")
    parser.add_argument('--host', default='localhost', help='Server host (default: localhost)')
    parser.add_argument('--port', type=int, default=5000, help='Server port (default: 5000)')
    args = parser.parse_args()

    navigator = Navigator(args.host, args.port)
    navigator.run()


if __name__ == "__main__":
    main()
