#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Proper streaming test - waits for start response, then sends stop
"""
import sys
import os
import json
import time
import socket
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src', 'streamingserver'))

from navigator import Navigator

def proper_streaming_test():
    print("🎯 PROPER STREAMING TEST")
    print("="*50)
    print("Flow: Start Command → Wait for Start Response → Stop Command")
    print("="*50)
    
    navigator = Navigator("localhost", 5000)
    
    try:
        # Connect and get to streaming
        print("🔌 Connecting...")
        if not navigator.connect():
            return False
        print("✅ Connected")
        
        print("📋 Getting providers...")
        if not navigator.get_providers():
            return False
            
        # Find PlutoTV
        pluto_provider = None
        for provider in navigator.providers:
            if provider.get('provider_id') == 'PlutoTV':
                pluto_provider = provider
                break
                
        if not pluto_provider:
            print("❌ PlutoTV not found")
            return False
            
        navigator.selected_provider = pluto_provider
        
        # Get categories and media
        if not navigator.get_categories():
            return False
        navigator.selected_category = navigator.categories[0]
        
        if not navigator.get_media_items():
            return False
            
        media_item = navigator.media_items[0]
        title = media_item.get('title', 'Unknown')
        print(f"🎬 Selected: {title}")
        
        # Prepare start command
        args = {
            "url": media_item.get('url', ''),
            "rec_dir": "/tmp",
            "show_ads": False,
            "buffering": 5,
            "av1": True,
            "quality": "best",
            "provider": pluto_provider,
            "data_dir": "/home/alpha/streamserver"
        }
        
        # Step 1: Send start command (don't expect immediate response)
        print("\n1️⃣ Sending start command...")
        
        # Send the command as raw JSON to avoid waiting for response
        import json as json_module
        start_cmd = ["start", args]
        cmd_json = json_module.dumps(start_cmd) + '\n'
        navigator.socket.sendall(cmd_json.encode('utf-8'))
        print("   ✅ Start command sent to server")
        
        # Step 2: Wait for async start broadcast using Navigator's async handling
        print("\n2️⃣ Waiting for start broadcast (buffering completion)...")
        print("   This may take 10-30 seconds as server buffers segments...")
        
        # Use Navigator's built-in response handling which processes async messages
        navigator.socket.settimeout(60)  # Long timeout for buffering
        start_broadcast_received = False
        
        # Keep calling send_command with a dummy command to trigger async message processing
        # This will allow _handle_async_message to process the start broadcast
        try:
            for attempt in range(60):  # 60 seconds max
                try:
                    # Use the navigator's response handling to process async messages
                    response_data = b''
                    chunk = navigator.socket.recv(8192)
                    if chunk:
                        response_data += chunk
                        
                        # Process any complete messages
                        if b'\n' in response_data:
                            parts = response_data.split(b'\n')
                            for line in parts[:-1]:
                                if line.strip():
                                    try:
                                        message = json_module.loads(line.decode('utf-8').strip())
                                        if isinstance(message, list) and len(message) >= 2:
                                            if message[0] == "start":
                                                print(f"🎉 START BROADCAST RECEIVED!")
                                                print(f"   Details: {message[1]}")
                                                start_broadcast_received = True
                                                break
                                    except:
                                        continue
                    
                    if start_broadcast_received:
                        break
                        
                    time.sleep(0.5)
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"   Error in attempt {attempt}: {e}")
                    continue
                    
        except Exception as e:
            print(f"💥 Error waiting for start: {e}")
            return False
            
        if not start_broadcast_received:
            print("❌ Never received start broadcast")
            return False
            
        # Step 3: Now send stop command
        print("\n3️⃣ Sending stop command...")
        stop_response = navigator.send_command("stop", {})
        print(f"   Stop response: {stop_response}")
        
        print("\n🎉 COMPLETE STREAMING TEST SUCCESS!")
        print("✅ Start command sent")
        print("✅ Start broadcast received (buffering complete)")
        print("✅ Stop command sent")
        print("✅ Full streaming workflow validated!")
        
        return True
        
    except Exception as e:
        print(f"💥 Test failed: {e}")
        return False
    finally:
        navigator.disconnect()

if __name__ == "__main__":
    success = proper_streaming_test()
    print(f"\n{'SUCCESS' if success else 'FAILED'}")
    exit(0 if success else 1)
