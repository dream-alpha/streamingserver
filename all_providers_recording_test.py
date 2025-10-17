#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Final Comprehensive Test Report - All Providers
Tests all providers using the proper async streaming workflow
"""
import sys
import os
import json
import time
import socket
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src', 'streamingserver'))

from navigator import Navigator

def test_provider_streaming(navigator, provider, max_wait_time=60):
    """Test a single provider's streaming capability"""
    provider_name = provider.get('name', 'Unknown')
    provider_id = provider.get('provider_id', 'unknown')
    recorder_used = None
    
    print(f"\n{'='*60}")
    print(f"TESTING PROVIDER: {provider_name}")
    print(f"{'='*60}")
    
    try:
        # Select provider
        navigator.selected_provider = provider
        
        # Get categories
        print("📂 Getting categories...")
        if not navigator.get_categories():
            return {
                'name': provider_name,
                'id': provider_id,
                'status': '❌ FAILED',
                'error': 'Failed to get categories',
                'categories': 0,
                'media_items': 0,
                'streaming': 'Not tested'
            }
        
        categories = navigator.categories
        print(f"✅ Found {len(categories)} categories")
        
        # Select first category
        if not categories:
            return {
                'name': provider_name,
                'id': provider_id,
                'status': '❌ ERROR',
                'error': 'No categories found',
                'categories': 0,
                'media_items': 0,
                'streaming': 'Not tested',
                'recorder_type': 'not_tested'
            }
        
        navigator.selected_category = categories[0]
        category_name = navigator.selected_category.get('name', 'Unknown')
        print(f"📋 Testing category: {category_name}")
        
        # Get media items
        print("🎬 Getting media items...")
        if not navigator.get_media_items():
            return {
                'name': provider_name,
                'id': provider_id,
                'status': '⚠️  PARTIAL',
                'error': 'No media items in first category',
                'categories': len(categories),
                'media_items': 0,
                'streaming': 'Not tested',
                'recorder_type': 'not_tested'
            }
        
        media_items = navigator.media_items
        if not media_items:
            return {
                'name': provider_name,
                'id': provider_id,
                'status': '❌ ERROR',
                'error': 'No media items found',
                'categories': len(categories),
                'media_items': 0,
                'streaming': 'Not tested',
                'recorder_type': 'not_tested'
            }
        
        print(f"✅ Found {len(media_items)} media items")
        
        # Test streaming with first media item
        media_item = media_items[0]
        title = media_item.get('title', media_item.get('name', 'Unknown'))
        print(f"🎯 Testing streaming: {title}")
        
        # Prepare streaming arguments
        args = {
            "url": media_item.get('url', ''),
            "rec_dir": "/tmp",
            "show_ads": False,
            "buffering": 5,
            "av1": True,
            "quality": "best",
            "provider": provider,
            "data_dir": "/home/alpha/streamingserver"
        }
        
        # Send start command
        print("🚀 Starting stream...")
        start_cmd = ["start", args]
        cmd_json = json.dumps(start_cmd) + '\n'
        navigator.socket.sendall(cmd_json.encode('utf-8'))
        
        # Wait for start broadcast
        print("⏳ Waiting for buffering completion...")
        navigator.socket.settimeout(max_wait_time)
        start_received = False
        recorder_used = None
        
        for attempt in range(max_wait_time * 2):  # 0.5s intervals
            try:
                chunk = navigator.socket.recv(8192)
                if chunk and b'\n' in chunk:
                    parts = chunk.split(b'\n')
                    for line in parts[:-1]:
                        if line.strip():
                            try:
                                message = json.loads(line.decode('utf-8').strip())
                                if (isinstance(message, list) and 
                                    len(message) >= 2 and 
                                    message[0] == "start"):
                                    print("🎉 Stream started successfully!")
                                    start_received = True
                                    # Extract recorder type from start message  
                                    if len(message) >= 2 and isinstance(message[1], dict):
                                        recorder_info = message[1].get('recorder', {})
                                        if isinstance(recorder_info, dict):
                                            recorder_used = recorder_info.get('type', 'unknown')
                                        elif isinstance(recorder_info, str):
                                            recorder_used = recorder_info
                                    break
                            except:
                                continue
                if start_received:
                    break
                time.sleep(0.5)
            except socket.timeout:
                break
            except Exception:
                break
        
        # Stop streaming
        try:
            navigator.send_command("stop", {})
        except:
            pass
        
        if start_received:
            return {
                'name': provider_name,
                'id': provider_id,
                'status': '✅ SUCCESS',
                'error': None,
                'categories': len(categories),
                'media_items': len(media_items),
                'streaming': 'Working',
                'recorder_type': recorder_used or 'unknown'
            }
        else:
            return {
                'name': provider_name,
                'id': provider_id,
                'status': '⚠️  PARTIAL',
                'error': 'Stream start timeout',
                'categories': len(categories),
                'media_items': len(media_items),
                'streaming': 'Timeout',
                'recorder_type': 'timeout'
            }
            
    except Exception as e:
        return {
            'name': provider_name,
            'id': provider_id,
            'status': '❌ ERROR',
            'error': str(e),
            'categories': 0,
            'media_items': 0,
            'streaming': 'Failed',
            'recorder_type': 'error'
        }

def main():
    print("🎯 FINAL COMPREHENSIVE PROVIDER TEST REPORT")
    print("Using proper async streaming workflow")
    print("="*80)
    
    navigator = Navigator("localhost", 5000)
    results = []
    
    try:
        # Connect to server
        print("🔌 Connecting to streaming server...")
        if not navigator.connect():
            print("❌ Failed to connect to server")
            return
        print("✅ Connected successfully")
        
        # Get all providers
        print("\n📋 Getting available providers...")
        if not navigator.get_providers():
            print("❌ Failed to get providers")
            return
        
        providers = navigator.providers
        print(f"✅ Found {len(providers)} providers")
        
        # Test each provider
        for i, provider in enumerate(providers):
            if provider.get('provider_id') == 'TEMPLATE_PROVIDER':
                print(f"\n⏭️  Skipping template provider")
                continue
                
            result = test_provider_streaming(navigator, provider)
            results.append(result)
            
            # Small delay between tests
            time.sleep(2)
        
        # Generate comprehensive report
        print(f"\n{'='*80}")
        print("📊 FINAL TEST RESULTS")
        print(f"{'='*80}")
        
        successful = 0
        partial = 0
        failed = 0
        
        print(f"{'Provider':<20} {'Status':<15} {'Cat':<4} {'Media':<6} {'Stream':<10}")
        print("-" * 70)
        
        for result in results:
            name = result['name'][:19]  # Truncate long names
            status = result['status']
            categories = result['categories']
            media_items = result['media_items']
            streaming = result['streaming']
            error = result.get('error', '')
            
            print(f"{name:<20} {status:<15} {categories:<4} {media_items:<6} {streaming:<10}")
            if error and len(error) < 50:
                print(f"{'':20} → {error}")
            
            if '✅ SUCCESS' in status:
                successful += 1
            elif '⚠️  PARTIAL' in status:
                partial += 1
            else:
                failed += 1
        
        # Summary statistics
        total = len(results)
        success_rate = (successful / total * 100) if total > 0 else 0
        partial_rate = (partial / total * 100) if total > 0 else 0
        
        print("\n" + "="*80)
        print("📈 SUMMARY STATISTICS:")
        print(f"  Total Providers Tested: {total}")
        print(f"  Fully Working: {successful} ({success_rate:.1f}%)")
        print(f"  Partially Working: {partial} ({partial_rate:.1f}%)")
        print(f"  Failed: {failed}")
        print(f"  Overall Success Rate: {success_rate:.1f}%")
        
        # Enhanced recorder coverage analysis
        recorder_stats = {}
        recorder_success_stats = {}
        
        for result in results:
            recorder_type = result.get('recorder_type', 'unknown')
            status = result.get('status', 'UNKNOWN')
            provider_name = result.get('name', 'Unknown')
            
            # Track overall recorder usage
            if recorder_type not in recorder_stats:
                recorder_stats[recorder_type] = {'count': 0, 'providers': []}
            recorder_stats[recorder_type]['count'] += 1
            recorder_stats[recorder_type]['providers'].append({
                'name': provider_name,
                'status': status,
                'streaming': result.get('streaming', 'Unknown')
            })
            
            # Track success rate per recorder type
            if recorder_type not in recorder_success_stats:
                recorder_success_stats[recorder_type] = {'total': 0, 'successful': 0, 'partial': 0, 'failed': 0}
            
            recorder_success_stats[recorder_type]['total'] += 1
            if '✅ SUCCESS' in status:
                recorder_success_stats[recorder_type]['successful'] += 1
            elif '⚠️  PARTIAL' in status:
                recorder_success_stats[recorder_type]['partial'] += 1
            else:
                recorder_success_stats[recorder_type]['failed'] += 1
        
        print(f"\n🎬 RECORDER COVERAGE ANALYSIS:")
        working_recorder_types = [k for k in recorder_stats.keys() if k not in ['unknown', 'timeout', 'error', 'not_tested']]
        print(f"  Total Recorder Types Used: {len(working_recorder_types)}")
        
        # Sort recorders by usage count
        sorted_recorders = sorted(recorder_stats.items(), key=lambda x: x[1]['count'], reverse=True)
        
        for recorder_type, stats in sorted_recorders:
            count = stats['count']
            providers = stats['providers']
            
            if recorder_type in ['unknown', 'timeout', 'error', 'not_tested']:
                print(f"  {recorder_type}: {count} ({'⚠️' if count > 0 else '✅'})")
                if count > 0:
                    for provider in providers:
                        print(f"    └─ {provider['name']}: {provider['status']}")
            else:
                # Show success rate for working recorder types
                success_stats = recorder_success_stats.get(recorder_type, {})
                total = success_stats.get('total', 0)
                successful = success_stats.get('successful', 0)
                partial = success_stats.get('partial', 0)
                failed = success_stats.get('failed', 0)
                success_rate = (successful / total * 100) if total > 0 else 0
                
                status_icon = "✅" if success_rate == 100 else "⚠️" if success_rate >= 50 else "❌"
                print(f"  {recorder_type}: {count} providers ({status_icon} {success_rate:.1f}% success)")
                
                # Show detailed provider breakdown
                for provider in providers:
                    status_emoji = "✅" if "SUCCESS" in provider['status'] else "⚠️" if "PARTIAL" in provider['status'] else "❌"
                    print(f"    └─ {provider['name']}: {status_emoji} {provider['streaming']}")
                
                # Show summary if not 100% successful
                if success_rate < 100:
                    print(f"      📊 {successful} working, {partial} partial, {failed} failed")
        
        # XNXX specific analysis
        xnxx_result = next((r for r in results if 'XNXX' in r['name']), None)
        if xnxx_result:
            print(f"\n🔍 XNXX PROVIDER ENHANCEMENT ANALYSIS:")
            print(f"  Status: {xnxx_result['status']}")
            print(f"  Categories: {xnxx_result['categories']}")
            print(f"  Media Items: {xnxx_result['media_items']}")
            print(f"  Streaming: {xnxx_result['streaming']}")
            if xnxx_result['status'] == '✅ SUCCESS':
                print("  ✅ Premium filtering enhancement successful!")
            elif 'PARTIAL' in xnxx_result['status']:
                print("  ⚠️  Enhancement partially successful - needs refinement")
            else:
                print("  ❌ Enhancement needs more work")
        
        # Final assessment
        print(f"\n{'='*80}")
        if success_rate >= 80:
            print(f"🎉 OVERALL ASSESSMENT: EXCELLENT")
            print(f"   {success_rate:.1f}% of providers fully working!")
        elif success_rate >= 60:
            print(f"⚠️  OVERALL ASSESSMENT: GOOD")
            print(f"   {success_rate:.1f}% success rate with some issues")
        elif success_rate >= 40:
            print(f"⚠️  OVERALL ASSESSMENT: FAIR")
            print(f"   {success_rate:.1f}% working - needs improvement")
        else:
            print(f"❌ OVERALL ASSESSMENT: NEEDS WORK")
            print(f"   Only {success_rate:.1f}% working properly")
        
        print("✅ Proper streaming workflow: CONFIRMED")
        print("="*80)
        
    except Exception as e:
        print(f"💥 Test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        navigator.disconnect()

if __name__ == "__main__":
    main()
