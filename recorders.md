# Recorder System Specification

## Overview
Multi-threaded recording system for downloading streaming media in various formats. Recorders receive resolved URLs and sessions from resolvers, then download and process streams into playable files.

## Purpose
- Download video streams in multiple formats (MP4, HLS/TS, HLS/M4S)
- Handle both VOD (Video on Demand) and live streams
- Provide progress feedback and playback coordination
- Manage thread lifecycle and resource cleanup
- Support authenticated downloads via session reuse

---

## Architecture

### Component Hierarchy

```
BaseRecorder (abstract base)
├── MP4_Recorder (direct MP4 downloads)
├── HLS_Recorder_Basic (standard HLS with TS segments)
├── HLS_Recorder_Live (live HLS streams)
└── HLS_Recorder_M4S (HLS with fragmented MP4 segments)
```

### Recorder Selection Flow

1. **Resolver determines recorder type**:
   ```python
   recorder_id = resolver.determine_recorder_type(resolved_url)
   # Returns: "mp4", "hls_basic", "hls_live", or "hls_m4s"
   ```

2. **Recorder manager creates instance**:
   ```python
   recorder = recorder_manager.start_recorder(recorder_id, resolve_result)
   ```

3. **Recorder receives resolve_result**:
   ```python
   {
       "resolved_url": "https://cdn.example.com/video.mp4",
       "session": <authenticated Session object>,
       "auth_tokens": {...},
       "rec_dir": "/tmp/recordings",
       "buffering": 5,
       "socketserver": <SocketServer instance>,
       "recorder_id": "mp4"
   }
   ```

4. **Recorder downloads and saves**:
   - Extract session and URL from resolve_result
   - Download stream using authenticated session
   - Save to `rec_dir/stream_0.{ext}`
   - Report progress via socketserver

---

## Base Recorder: `BaseRecorder`

### Purpose
Provides common threading, lifecycle management, and messaging for all recorders.

### Location
`base_recorder.py`

### Key Attributes

```python
self.name: str                      # Recorder name (class name)
self.socketserver: SocketServer     # For client communication
self.is_running: bool               # Thread running state
self.thread: threading.Thread       # Recording thread
self.stop_event: threading.Event    # Stop signal
self.stop_sent: bool                # Prevents duplicate stop messages
```

### Key Methods

#### `__init__(name: str = None, socketserver=None)`

**Purpose**: Initialize base recorder state

**Parameters**:
- `name`: Recorder name (defaults to class name)
- `socketserver`: Socket server for client communication

**Actions**:
- Set name and socketserver
- Initialize threading state (is_running, stop_event, stop_sent)
- Log initialization

#### `start_thread(resolve_result) -> bool`

**Purpose**: Start recording in background thread

**Parameters**:
- `resolve_result`: Complete resolver result dict

**Returns**: `True` if started, `False` if already running

**Process**:
1. Check if already running → return False
2. Extract socketserver from resolve_result
3. Clear stop_event
4. Create thread with `_thread_wrapper` as target
5. Start thread (daemon=True)
6. Set is_running = True
7. Return True

**Thread Management**:
- Daemon thread (doesn't prevent process exit)
- Uses wrapper for guaranteed cleanup
- Non-blocking start

#### `_thread_wrapper(resolve_result)`

**Purpose**: Thread wrapper ensuring cleanup even on errors

**Process**:
1. Call child class `record_start(resolve_result)`
2. Catch all exceptions
3. On exception: Log error, call `on_thread_error()`
4. Always: Set is_running = False, call `on_thread_ended()`

**Guarantees**:
- `is_running` always cleared
- Error callbacks always called
- Cleanup always executed

#### `record_start(resolve_result)`

**Purpose**: Base recording setup (override in child classes)

**Default Implementation**:
```python
def record_start(self, resolve_result):
    # Clean up old files
    if resolve_result:
        pattern = os.path.join(resolve_result.get("rec_dir", "/tmp"), "stream*")
        subprocess.run(["rm", "-f"] + glob.glob(pattern), check=False)
    # Child classes override and call super().record_start(resolve_result) first
```

**Child Class Pattern**:
```python
def record_start(self, resolve_result):
    super().record_start(resolve_result)  # Cleanup
    # Extract parameters
    self.session = resolve_result.get("session")
    self.rec_dir = resolve_result.get("rec_dir")
    # Start recording logic
    self.record_stream(url, rec_dir)
```

#### `stop() -> bool`

**Purpose**: Stop recording and wait for thread completion

**Returns**: `True` on success

**Process**:
1. Check if running → return True if not
2. Set stop_sent = True (prevent completion messages)
3. Set stop_event (signal thread to stop)
4. Wait for thread with timeout (5 seconds)
5. Set is_running = False
6. Log completion
7. Return True

**Blocking**: Waits for thread to finish (up to 5 seconds)

#### `start_playback(video_url: str, output_file: str)`

**Purpose**: Notify client that recording started and playback can begin

**Parameters**:
- `video_url`: Original video URL
- `output_file`: Path to recording file

**Actions**:
1. Log playback start
2. Broadcast "start" message to client with:
   - `url`: Video URL
   - `rec_file`: Output file path
   - `section_index`: 0
   - `segment_index`: 0
   - `recorder.type`: Recorder type

**Timing**: Called after first chunk/segment downloaded

**Purpose**: Enables immediate playback while download continues

#### `on_thread_ended()`

**Purpose**: Cleanup when thread ends normally (override in child)

**Default Implementation**:
```python
def on_thread_ended(self):
    self.is_running = False
    logger.info(f"{self.name} has fully stopped")
```

#### `on_thread_error(error: Exception, error_id: str = "failure")`

**Purpose**: Handle recording errors (override in child)

**Parameters**:
- `error`: Exception that occurred
- `error_id`: Error type ("failure", "drm_protected", "timeout", etc.)

**Default Implementation**:
```python
def on_thread_error(self, error, error_id="failure"):
    self.is_running = False
    logger.error(f"{self.name} encountered an error: {error}")
    
    if self.socketserver and not self.stop_sent:
        recorder_type = self.name.lower().replace("_recorder", "")
        self.socketserver.broadcast([
            "stop", 
            {
                "reason": "error", 
                "error_id": error_id, 
                "msg": str(error), 
                "recorder": {"type": recorder_type}
            }
        ])
        self.stop_sent = True
```

**Error IDs**:
- `"failure"`: General failure
- `"drm_protected"`: DRM protection detected
- `"timeout"`: Connection timeout
- `"forbidden"`: 403 Forbidden (auth failure)

---

## MP4 Recorder: `MP4_Recorder`

### Purpose
Direct HTTP download of MP4 files with progress tracking and session reuse.

### Location
`mp4_recorder.py`

### Use Cases
- Direct MP4 video URLs
- Progressive download (not HLS/DASH)
- VOD content with known file size

### Additional Attributes

```python
self.progress: int              # Download progress percentage
self.total_size: int            # Total file size in bytes
self.downloaded_size: int       # Downloaded bytes
self.recording_has_started: bool  # Whether start_playback called
self.session: requests.Session  # Authenticated session from resolver
self.resolved_url: str          # Direct MP4 URL
self.auth_tokens: dict          # Auth tokens (optional)
self.rec_dir: str               # Output directory
```

### Implementation

#### `record_start(resolve_result)`

**Purpose**: Extract parameters and initiate download

**Process**:
```python
def record_start(self, resolve_result):
    super().record_start(resolve_result)  # Cleanup
    
    # Extract parameters
    self.resolved_url = resolve_result.get("resolved_url")
    self.auth_tokens = resolve_result.get("auth_tokens")
    self.session = resolve_result.get("session")
    self.rec_dir = resolve_result.get("rec_dir", "/tmp")
    
    # Fallback session
    if not self.session:
        logger.warning("No session from resolver - creating basic session")
        self.session = requests.Session()
    
    # Start download
    self.record_stream(self.resolved_url, self.rec_dir)
```

**Session Priority**:
1. Use session from resolver (authenticated)
2. Fallback: Create basic session

#### `record_stream(url: str, rec_dir: str)`

**Purpose**: Main download logic with progress tracking

**Process**:

1. **Setup**:
   ```python
   output_file = os.path.join(rec_dir, "stream_0.mp4")
   session = self.session
   if not session:
       raise ValueError("No session available")
   ```

2. **Get file size** (optional):
   ```python
   try:
       head_response = session.head(url, timeout=10)
       self.total_size = int(head_response.headers.get('Content-Length', 0))
   except Exception as e:
       logger.warning("HEAD request failed, proceeding without size")
       self.total_size = 0
   ```

3. **Check stop event** (early exit):
   ```python
   if self.stop_event.is_set():
       logger.info("Stop requested before download")
       return
   ```

4. **Stream download**:
   ```python
   with session.get(url, stream=True, timeout=60) as response:
       response.raise_for_status()  # Raise on 4xx/5xx
       
       self.downloaded_size = 0
       chunk_count = 0
       
       with open(output_file, 'wb') as f:
           for chunk in response.iter_content(chunk_size=8192):
               # Check stop
               if self.stop_event.is_set():
                   logger.info("Stop during download")
                   break
               
               # Write chunk
               if chunk:
                   f.write(chunk)
                   self.downloaded_size += len(chunk)
                   chunk_count += 1
                   
                   # First chunk - start playback
                   if chunk_count == 1:
                       if not self.recording_has_started:
                           self.recording_has_started = True
                           self.start_playback(url, output_file)
                   
                   # Progress reporting (every 2% or 3 seconds)
                   if self.total_size > 0:
                       progress = int((self.downloaded_size / self.total_size) * 100)
                       if progress - self.progress >= 2:
                           self.progress = progress
                           logger.info("Progress: %d%%", progress)
   ```

5. **Verify download**:
   ```python
   if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
       final_size = os.path.getsize(output_file)
       logger.info("Progress: 100%% (%s)", format_size(final_size))
       if not self.recording_has_started:
           self.start_playback(url, output_file)
       logger.info("Download completed")
   else:
       logger.error("File empty or missing")
       super().on_error("File empty or missing")
   ```

6. **Error handling**:
   ```python
   except Exception as e:
       logger.error("Download error: %s", e)
       super().on_error(f"MP4 download error: {e}")
   ```

### Progress Reporting

**Frequency**: Every 2% progress or 3 seconds (whichever comes first)

**Calculation**:
```python
progress = int((downloaded_size / total_size) * 100)
speed = bytes_since_last / elapsed_time
```

**Logging**:
```python
logger.info("Progress: %d%% (%s/%s, %.2f MB/s)",
            progress,
            format_size(downloaded_size),
            format_size(total_size),
            speed / 1024 / 1024)
```

### Error Cases

**403 Forbidden**:
```python
if response.status_code == 403:
    logger.error("403 Forbidden - missing/incorrect headers")
    logger.error("Session headers: %s", dict(session.headers))
    raise PermissionError(f"403 Forbidden - CDN access denied")
```

**Empty File**:
```python
if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
    logger.error("File empty or missing")
    super().on_error("File empty or missing")
```

**Size Mismatch**:
```python
if abs(actual_size - total_size) > 100000:  # 100KB tolerance
    logger.warning("Size mismatch: %d vs expected %d", actual_size, total_size)
    # Continue anyway - file might be usable
```

---

## HLS Basic Recorder: `HLS_Recorder_Basic`

### Purpose
Record standard HLS streams with Transport Stream (TS) segments for VOD content.

### Location
`hls_recorder_basic.py`

### Use Cases
- HLS playlists (.m3u8) with TS segments (.ts)
- VOD content (has EXT-X-ENDLIST tag)
- Standard segment-based streaming

### Additional Attributes

```python
self.channel_uri: str           # HLS playlist URL
self.session: requests.Session  # Authenticated session from resolver
self.rec_dir: str               # Output directory
self.buffering: int             # Buffering segments before playback
```

### Implementation

#### `record_start(resolve_result)`

**Purpose**: Extract parameters and start HLS recording

```python
def record_start(self, resolve_result):
    super().record_start(resolve_result)  # Cleanup
    
    # Extract parameters
    self.channel_uri = resolve_result.get("resolved_url")
    self.rec_dir = resolve_result.get("rec_dir", "/tmp")
    self.buffering = resolve_result.get("buffering", 5)
    self.session = resolve_result.get("session")
    
    if not self.session:
        raise ValueError("No session available for HLS recording")
    
    # Start recording loop
    self.record_stream(self.channel_uri, self.rec_dir, self.buffering)
```

#### `process_playlist_content(playlist_text: str) -> m3u8.M3U8`

**Purpose**: Parse and validate playlist content (override in specialized recorders)

**Process**:
```python
def process_playlist_content(self, playlist_text):
    playlist = m3u8.loads(playlist_text)
    
    # Check for DRM protection
    if detect_drm_in_content(playlist_text, content_type="m3u8")["has_drm"]:
        raise ValueError("DRM_PROTECTED: Stream uses DRM protection")
    
    return playlist
```

**Returns**: Parsed m3u8.M3U8 object

**Raises**: ValueError if DRM detected

#### `handle_master_playlist(playlist, base_url) -> str`

**Purpose**: Select best quality stream from master playlist

**Process**:
```python
def handle_master_playlist(self, playlist, base_url):
    if not playlist.playlists:
        logger.error("Master playlist has no streams")
        return None
    
    # Sort by bandwidth (highest first)
    sorted_streams = sorted(playlist.playlists, 
                          key=lambda x: x.stream_info.bandwidth, 
                          reverse=True)
    
    # Select highest quality
    best_stream = sorted_streams[0]
    resolution = best_stream.stream_info.resolution
    bandwidth = best_stream.stream_info.bandwidth
    
    logger.info("Selected: %s, bandwidth: %d", resolution, bandwidth)
    
    # Construct absolute URL
    if best_stream.uri.startswith('http'):
        return best_stream.uri
    
    # Relative URL - join with base
    base_dir = '/'.join(base_url.split('/')[:-1])
    return base_dir + '/' + best_stream.uri
```

**Returns**: URL of selected media playlist

#### `should_reload_master_playlist(playlist) -> bool`

**Purpose**: Determine if master playlist reload needed (override for live streams)

**Base Implementation**:
```python
def should_reload_master_playlist(self, playlist):
    return False  # VOD streams never need reload
```

**Live Override**: Returns True if playlist has no endlist

#### `calculate_sleep_duration(target_duration) -> float`

**Purpose**: Calculate sleep time between playlist fetches (override for live streams)

**Base Implementation**:
```python
def calculate_sleep_duration(self, target_duration):
    return min(target_duration / 2, 3.0) if target_duration else 1.0
```

**Returns**: Sleep duration in seconds

#### `record_stream(channel_uri: str, rec_dir: str, buffering: int)`

**Purpose**: Main recording loop for HLS streams

**State Variables**:
```python
segment_index = 0               # Current segment index
section_index = -1              # Current section index
empty_playlist_count = 0        # Consecutive empty playlists
failed_playlist_count = 0       # Consecutive failed fetches
reload_master_playlist = True   # Whether to reload master
media_playlist_url = None       # Current media playlist URL
last_sequence = None            # Last processed sequence number
failed_segment_count = 0        # Consecutive failed segments
segment_processor = None        # HLSSegmentProcessor instance
```

**Main Loop**:

1. **Master Playlist Loading** (if needed):
   ```python
   if reload_master_playlist:
       media_playlist_url = channel_uri  # Use resolved URL directly
       
       # Create segment processor
       if segment_processor is None:
           segment_processor = HLSSegmentProcessor(
               rec_dir, 
               self.socketserver, 
               media_playlist_url, 
               "hls_basic"
           )
       
       reload_master_playlist = False
   ```

2. **Fetch Playlist**:
   ```python
   playlist_text = get_playlist(self.session, media_playlist_url)
   if not playlist_text:
       failed_playlist_count += 1
       if failed_playlist_count >= max_failed_playlists:
           reload_master_playlist = True
       continue
   ```

3. **Parse Playlist**:
   ```python
   playlist = self.process_playlist_content(playlist_text)
   ```

4. **Handle Master Playlist** (if detected):
   ```python
   if hasattr(playlist, 'playlists') and playlist.playlists:
       selected_url = self.handle_master_playlist(playlist, media_playlist_url)
       if selected_url:
           media_playlist_url = selected_url
           continue  # Fetch media playlist
   ```

5. **Check Empty Playlist**:
   ```python
   if not playlist.segments:
       empty_playlist_count += 1
       if empty_playlist_count >= max_empty_playlists:
           reload_master_playlist = True
       sleep_duration = self.calculate_sleep_duration(target_duration)
       time.sleep(sleep_duration)
       continue
   ```

6. **Process Segments**:
   ```python
   sequence_start = getattr(playlist, 'media_sequence', 0)
   is_vod = hasattr(playlist, 'is_endlist') and playlist.is_endlist
   
   # Adjust buffering for VOD with few segments
   effective_buffering = buffering
   if is_vod and len(playlist.segments) < buffering:
       effective_buffering = len(playlist.segments)
   
   for idx, segment in enumerate(playlist.segments):
       if self.stop_event.is_set():
           break
       
       sequence = sequence_start + idx
       
       # Skip already processed (for live streams)
       if not is_vod and last_sequence is not None and sequence <= last_sequence:
           continue
       
       # Process segment
       segment = segment_processor.process_segment(
           self.session, 
           target_duration, 
           effective_buffering, 
           segment
       )
       
       if segment is None:
           failed_segment_count += 1
           if failed_segment_count >= 5:
               self.stop_event.set()
       else:
           failed_segment_count = 0
       
       last_sequence = sequence
   ```

7. **Check VOD Completion**:
   ```python
   if hasattr(playlist, 'is_endlist') and playlist.is_endlist:
       logger.info("VOD complete (EXT-X-ENDLIST)")
       if self.socketserver:
           self.socketserver.broadcast([
               "stop", 
               {"reason": "complete", "channel": channel_uri, "rec_dir": rec_dir}
           ])
       self.stop_event.set()
       break
   ```

8. **Sleep Before Next Fetch**:
   ```python
   sleep_duration = self.calculate_sleep_duration(target_duration)
   time.sleep(sleep_duration)
   ```

**Error Handling**:
```python
except KeyboardInterrupt:
    logger.info("Interrupted by user")
except Exception as e:
    error_str = str(e)
    if error_str.startswith("DRM_PROTECTED:"):
        error_id = "drm_protected"
    else:
        error_id = "failure"
    
    if self.socketserver:
        self.socketserver.broadcast([
            "stop", 
            {"reason": "error", "error_id": error_id, "channel": channel_uri}
        ])
finally:
    if segment_processor and hasattr(segment_processor, 'ffmpeg_proc'):
        terminate_ffmpeg_process(segment_processor.ffmpeg_proc)
```

### Segment Processing

**Handled by**: `HLSSegmentProcessor` (see separate section)

**Process**:
1. Resolve relative segment URLs to absolute
2. Download segment data via authenticated session
3. Validate TS segment format
4. Check for DRM protection
5. Process segment properties (resolution, duration, PTS)
6. Handle section changes (resolution changes)
7. Append segment to output file or pipe to FFmpeg
8. Send buffering complete message after N segments

---

## HLS Live Recorder: `HLS_Recorder_Live`

### Purpose
Record live HLS streams that continuously update playlists with new segments.

### Location
`hls_recorder_live.py`

### Use Cases
- Live HLS streams (no EXT-X-ENDLIST)
- Continuously updating playlists
- Real-time segment processing

### Differences from Basic Recorder

**Playlist Reload**:
- Continuously fetches updated playlists
- Detects endlist → triggers master reload

**Segment Tracking**:
- Uses `media_sequence` to track processed segments
- Only processes new segments (sequence > last_sequence)

**Master Playlist**:
- Actively uses `get_master_playlist()` to resolve best quality
- Reloads master on errors or endlist detection

### Implementation

#### `record_start(resolve_result)`

Same as Basic, but logs "live HLS recording"

#### `record_stream(channel_uri: str, rec_dir: str, buffering: int)`

**Differences from Basic**:

1. **Master Playlist Loading**:
   ```python
   if reload_master_playlist:
       media_playlist_url = get_master_playlist(self.session, channel_uri)
       # Returns URL of best quality stream
   ```

2. **Endlist Detection**:
   ```python
   if playlist.is_endlist:
       reload_master_playlist = True  # Stream ended, try reload
       time.sleep(1)
       continue
   ```

3. **Segment Filtering**:
   ```python
   sequence = sequence_start + idx
   if last_sequence is not None and sequence <= last_sequence:
       continue  # Already processed - skip
   ```

4. **No VOD Completion Check**:
   - No check for endlist completion
   - Runs until manually stopped

5. **Continuous Operation**:
   - Always reloads playlist
   - Never exits on endlist
   - Tracks last_sequence to avoid reprocessing

---

## HLS M4S Recorder: `HLS_Recorder_M4S`

### Purpose
Record HLS streams with fragmented MP4 (M4S/MP4) segments using FFmpeg transcoding.

### Location
`hls_recorder_m4s.py`

### Use Cases
- HLS with .m4s segments (fragmented MP4)
- HLS with .mp4 segments
- HLS with AV1 or HEVC codecs requiring transcoding
- Dreambox-compatible output (transcoded to H.264 TS)

### Architecture Difference

**Delegation to FFmpeg**:
- Does NOT manually download segments
- Passes M3U8 URL directly to FFmpeg
- FFmpeg handles playlist parsing and segment downloading
- Recorder monitors FFmpeg process

### Additional Attributes

```python
self.ffmpeg_process: subprocess.Popen  # FFmpeg process handle
```

### Implementation

#### `record_start(resolve_result)`

**Purpose**: Extract parameters and start FFmpeg recording

```python
def record_start(self, resolve_result):
    super().record_start(resolve_result)  # Cleanup
    
    # Extract parameters
    channel_uri = resolve_result.get("resolved_url")
    ffmpeg_headers = resolve_result.get("ffmpeg_headers")
    rec_dir = resolve_result.get("rec_dir", "/tmp")
    
    # Validate
    if not channel_uri:
        raise ValueError("No channel URI for M4S recording")
    
    # Start FFmpeg recording
    self.record_stream(channel_uri, rec_dir, ffmpeg_headers)
```

#### `record_stream(channel_uri: str, rec_dir: str, ffmpeg_headers: str = None)`

**Purpose**: Start FFmpeg and monitor process

**Process**:

1. **Setup Output**:
   ```python
   output_file = os.path.join(rec_dir, "stream_0.ts")
   logger.info("Using TS output for universal transcoding")
   ```

2. **Start FFmpeg**:
   ```python
   self.ffmpeg_process = self._start_ffmpeg(
       channel_uri, 
       output_file, 
       ffmpeg_headers
   )
   ```

3. **Monitor Process**:
   ```python
   count_up = 0
   while not self.stop_event.is_set():
       # Check if FFmpeg exited
       if self.ffmpeg_process.poll() is not None:
           return_code = self.ffmpeg_process.returncode
           
           # Capture stderr for error details
           _stdout, stderr = self.ffmpeg_process.communicate(timeout=5)
           if stderr:
               stderr_text = stderr.decode('utf-8', errors='ignore')
               logger.error("FFmpeg stderr: %s", stderr_text)
               
               # Check for specific errors
               if "403" in stderr_text or "Forbidden" in stderr_text:
                   logger.error("FFmpeg got 403 - auth failed")
               elif "404" in stderr_text:
                   logger.error("FFmpeg got 404 - URL invalid")
           
           if return_code == 0:
               logger.info("FFmpeg completed successfully")
           else:
               logger.error("FFmpeg failed with code %d", return_code)
               if self.socketserver:
                   self.socketserver.broadcast([
                       "stop",
                       {"reason": "error", "message": f"FFmpeg failed: {return_code}"}
                   ])
           break
       
       # Trigger playback after initial buffering
       if count_up == 10:
           self.start_playback(channel_uri, output_file)
       count_up += 1
       
       time.sleep(1)  # Check every second
   ```

4. **Cleanup**:
   ```python
   finally:
       self._cleanup()
       logger.info("M4S recording stopped")
   ```

#### `_start_ffmpeg(input_url: str, output_file: str, ffmpeg_headers: str = None) -> subprocess.Popen`

**Purpose**: Build FFmpeg command and start process

**FFmpeg Command Structure**:

```python
cmd = ['ffmpeg', '-y']

# Add authentication headers
if ffmpeg_headers:
    cmd += ['-headers', ffmpeg_headers]

# Universal transcoding for Dreambox compatibility
cmd += [
    '-i', input_url,
    
    # Video transcoding (H.264 Baseline)
    '-c:v', 'libx264',
    '-profile:v', 'baseline',
    '-level:v', '3.0',
    '-preset', 'ultrafast',
    '-b:v', '1200k',
    '-maxrate', '1200k',
    '-bufsize', '2400k',
    '-pix_fmt', 'yuv420p',
    '-vf', 'scale=1280:720:flags=fast_bilinear',
    '-r', '24',
    '-g', '48',
    '-refs', '1',
    '-tune', 'zerolatency',
    '-slices', '8',
    '-threads', '0',
    '-x264opts', 'bframes=0:cabac=0:weightp=0:8x8dct=0:aud=1:me=dia:subme=1:trellis=0',
    
    # Audio transcoding (AAC)
    '-c:a', 'aac',
    '-bsf:a', 'aac_adtstoasc',
    '-b:a', '128k',
    '-ar', '48000',
    '-ac', '2',
    
    # MPEG-TS container for Dreambox
    '-f', 'mpegts',
    '-mpegts_copyts', '1',
    '-mpegts_start_pid', '0x100',
    '-mpegts_m2ts_mode', '0',
    '-mpegts_pmt_start_pid', '0x1000',
    '-mpegts_original_network_id', '1',
    '-mpegts_service_id', '1',
    '-muxrate', '2000000',
    '-flush_packets', '1',
    '-fflags', '+genpts+igndts',
    '-max_muxing_queue_size', '1024',
    '-max_delay', '0',
    
    output_file
]
```

**Process Creation**:
```python
logger.info("Starting FFmpeg: %s", ' '.join(cmd))
process = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    stdin=subprocess.DEVNULL
)
return process
```

**Transcoding Rationale**:
- Always transcode to H.264 Baseline (maximum compatibility)
- Use MPEG-TS container (better for Dreambox)
- Ultra-fast preset for real-time processing
- Low latency settings for streaming playback

#### `_cleanup()`

**Purpose**: Terminate FFmpeg process on stop

```python
def _cleanup(self):
    if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
        logger.info("Terminating FFmpeg process...")
        self.ffmpeg_process.terminate()
        try:
            self.ffmpeg_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("FFmpeg did not terminate, killing...")
            self.ffmpeg_process.kill()
```

---

## Recorder Manager: `Recorder`

### Purpose
Manages single active recorder instance, ensuring only one runs at a time.

### Location
`recorder.py`

### Attributes

```python
self.current_recorder: BaseRecorder | None  # Active recorder
self.recorder_types: dict                   # Type name → class mapping
```

### Type Mapping

```python
self.recorder_types = {
    'mp4': MP4_Recorder,
    'hls_basic': HLS_Recorder_Basic,
    'hls_live': HLS_Recorder_Live,
    'hls_m4s': HLS_Recorder_M4S
}
```

### Key Methods

#### `start_recorder(recorder_type: str, resolve_result: dict) -> bool`

**Purpose**: Start recorder by type name

**Parameters**:
- `recorder_type`: One of "mp4", "hls_basic", "hls_live", "hls_m4s"
- `resolve_result`: Complete resolver result dict

**Returns**: `True` if started successfully

**Process**:
```python
def start_recorder(self, recorder_type, resolve_result):
    # Validate type
    if recorder_type not in self.recorder_types:
        logger.error("Unknown recorder type: %s", recorder_type)
        return False
    
    # Stop current recorder and wait
    if self.current_recorder and self.current_recorder.is_running:
        logger.info("Stopping current recorder...")
        if not self.stop():
            logger.error("Failed to stop current recorder")
            return False
    
    # Create and start new recorder
    recorder_class = self.recorder_types[recorder_type]
    self.current_recorder = recorder_class()
    return self.current_recorder.start_thread(resolve_result)
```

**Blocking**: Waits for previous recorder to stop before starting new one

#### `stop() -> bool`

**Purpose**: Stop current recorder and wait for completion

**Returns**: `True` if stopped successfully

```python
def stop(self):
    if self.current_recorder and self.current_recorder.is_running:
        logger.info("Stopping %s...", self.current_recorder.name)
        success = self.current_recorder.stop()  # Blocks until stopped
        if success:
            logger.info("%s stopped", self.current_recorder.name)
        return success
    return True  # No recorder running
```

#### `status() -> str`

**Purpose**: Get current recorder status

**Returns**: Status string

```python
def status(self):
    if self.current_recorder and self.current_recorder.is_running:
        return f"Running: {self.current_recorder.name}"
    return "No recorder running"
```

#### `get_available_types() -> list`

**Purpose**: List available recorder types

**Returns**: List of type strings

```python
def get_available_types(self):
    return list(self.recorder_types.keys())
```

#### `record_stream(resolve_result: dict)`

**Purpose**: Main entry point for recording

**Process**:
```python
def record_stream(self, resolve_result):
    # Extract data
    channel_uri = resolve_result.get("resolved_url")
    recorder_id = resolve_result.get("recorder_id")
    
    # Validate recorder_id
    if not recorder_id:
        logger.error("No recorder_id in resolve_result - required!")
        return
    
    logger.info("Using recorder: %s (from resolver)", recorder_id)
    
    # Start recorder
    self.start_recorder(recorder_id, resolve_result)
```

**Note**: Resolver must specify `recorder_id` in result

---

## HLS Segment Processor: `HLSSegmentProcessor`

### Purpose
Process individual HLS segments (download, validate, append to output).

### Location
`hls_segment_processor.py`

### Shared by
- HLS_Recorder_Basic
- HLS_Recorder_Live

### Attributes

```python
self.rec_dir: str                  # Recording directory
self.socketserver: SocketServer    # For messaging
self.playlist_base_url: str        # Base URL for relative segments
self.recorder_type: str            # "hls_basic" or "hls_live"

# State tracking
self.segment_index: int            # Current segment index
self.section_index: int            # Current section index
self.previous_uri: str             # Previous segment URI
self.previous_duration: float      # Previous segment duration
self.previous_pts: int             # Previous segment PTS
self.offset: int                   # Timing offset
self.continuous_pts: int           # Continuous PTS
self.cc_map: dict                  # Continuity counter map
self.section_file: str             # Current section file path
self.ffmpeg_proc: subprocess.Popen # FFmpeg process (if used)
self.buffering_completed: bool     # Whether buffering done
```

### Key Methods

#### `__init__(rec_dir, socketserver, playlist_base_url, recorder_type)`

**Purpose**: Initialize segment processor

**Parameters**:
- `rec_dir`: Recording directory
- `socketserver`: Socket server for messaging
- `playlist_base_url`: Base URL for resolving relative segment URLs
- `recorder_type`: "hls_basic" or "hls_live"

#### `_resolve_segment_url(segment_uri: str) -> str`

**Purpose**: Resolve relative segment URLs to absolute

**Process**:
```python
def _resolve_segment_url(self, segment_uri):
    # Already absolute
    if segment_uri.startswith('http'):
        return segment_uri
    
    # Join with base URL
    if self.playlist_base_url:
        resolved_url = urljoin(self.playlist_base_url, segment_uri)
        return resolved_url
    
    # Fallback (will likely fail)
    logger.warning("No base URL for relative segment: %s", segment_uri)
    return segment_uri
```

#### `process_segment(session, target_duration, buffering, segment) -> Segment | None`

**Purpose**: Process single HLS segment

**Parameters**:
- `session`: Authenticated session for download
- `target_duration`: Playlist target duration
- `buffering`: Number of segments before playback
- `segment`: m3u8.Segment object

**Returns**: Processed segment or None on failure

**Process**:

1. **Resolve URL**:
   ```python
   segment_url = self._resolve_segment_url(segment.uri)
   ```

2. **Extract Key Info** (for encrypted segments):
   ```python
   key_info = {"METHOD": None, "URI": None, "IV": None}
   if segment.key:
       key_info = {
           "METHOD": segment.key.method,
           "URI": segment.key.uri,
           "IV": segment.key.iv
       }
   ```

3. **Download Segment**:
   ```python
   segment_data = download_segment(
       session, 
       segment_url, 
       self.segment_index, 
       key_info, 
       max_retries=10, 
       timeout=5
   )
   ```

4. **Validate Segment**:
   ```python
   if not segment_data or not is_valid_ts_segment(segment_data):
       logger.error("Invalid segment %s", self.segment_index)
       
       # Check for DRM
       drm_result = comprehensive_drm_check(
           url=segment_url,
           content="",
           error_message="Failed to download segment",
           content_type="ts"
       )
       
       if drm_result.get("has_drm"):
           raise ValueError(f"DRM_PROTECTED: {drm_result.get('details')}")
       
       return None
   ```

5. **Check for DRM in Content**:
   ```python
   drm_result = detect_drm_in_content(segment_data, content_type="ts")
   if drm_result["has_drm"]:
       raise ValueError(f"DRM_PROTECTED: {drm_result['details']}")
   ```

6. **Extract Segment Properties**:
   ```python
   properties = get_segment_properties(segment_data, segment_url)
   resolution = properties.get("resolution")
   pts = properties.get("pts", 0)
   duration = segment.duration or target_duration
   ```

7. **Detect Section Change** (resolution change):
   ```python
   if resolution != self.previous_resolution:
       new_section = True
       self.section_index += 1
       logger.info("Section change detected: %s -> %s", 
                   self.previous_resolution, resolution)
   ```

8. **Process Segment Data**:
   ```python
   # Shift PTS if needed
   segment_data = shift_segment(segment_data, self.offset)
   
   # Update continuity counters
   segment_data, self.cc_map = update_continuity_counters(
       segment_data, 
       self.cc_map
   )
   ```

9. **Append to Output**:
   ```python
   if new_section:
       # Create new section file
       self.section_file = os.path.join(
           self.rec_dir, 
           f"stream_{self.section_index}.ts"
       )
   
   # Append segment data
   append_to_rec_file(self.section_file, segment_data)
   ```

10. **Update State**:
    ```python
    self.previous_uri = segment_url
    self.previous_duration = duration
    self.previous_pts = pts
    self.previous_resolution = resolution
    self.segment_index += 1
    ```

11. **Buffering Complete Message**:
    ```python
    if not self.buffering_completed and self.segment_index >= buffering:
        self.buffering_completed = True
        if self.socketserver:
            self.socketserver.broadcast([
                "start",
                {
                    "url": segment_url,
                    "rec_file": self.section_file,
                    "section_index": self.section_index,
                    "segment_index": self.segment_index,
                    "recorder": {"type": self.recorder_type}
                }
            ])
    ```

12. **Return**:
    ```python
    return segment
    ```

---

## Resolve Result Schema

### Purpose
Standard data structure passed from resolver to recorder via recorder manager.

### Complete Schema

```python
{
    # Required fields
    "resolved_url": str,              # Streamable URL (MP4, M3U8, etc.)
    "recorder_id": str,               # "mp4", "hls_basic", "hls_live", "hls_m4s"
    "rec_dir": str,                   # Recording output directory
    
    # Session and authentication
    "session": requests.Session,      # Authenticated session from resolver
    "auth_tokens": dict | None,       # Auth tokens dict from AuthTokens.to_dict()
    "ffmpeg_headers": str | None,     # FFmpeg-formatted headers (for M4S)
    
    # Resolver metadata
    "resolved": bool,                 # True if resolution succeeded
    "resolver": str,                  # Resolver name
    "quality": str,                   # Selected quality
    
    # Recording configuration
    "buffering": int,                 # Buffering segments (default: 5)
    "show_ads": bool,                 # Whether to include ads (default: False)
    
    # Socket server
    "socketserver": SocketServer,     # For progress/status messages
    
    # Optional metadata
    "original_url": str,              # Original video page URL
    "all_sources": list,              # All available sources
}
```

### Field Descriptions

**resolved_url**: Direct streamable URL
- MP4 recorder: Direct MP4 file URL
- HLS recorders: M3U8 playlist URL
- Must be absolute URL

**recorder_id**: Recorder type selection
- Determined by resolver via `determine_recorder_type()`
- Must be one of: "mp4", "hls_basic", "hls_live", "hls_m4s"
- Required field

**rec_dir**: Output directory
- Where recorded files are saved
- Format: `{rec_dir}/stream_{section_index}.{ext}`
- Must exist and be writable

**session**: Authenticated HTTP session
- Created by resolver during URL resolution
- Contains authentication cookies and headers
- Reused by recorder for downloads
- Fallback: Recorder creates basic session if None

**auth_tokens**: Authentication token dict
- From `AuthTokens.to_dict()`
- Contains headers, cookies, method
- Optional (None if no auth needed)

**ffmpeg_headers**: FFmpeg-formatted headers
- From `AuthTokens.get_ffmpeg_headers()`
- Format: "Header1: Value1\r\nHeader2: Value2\r\n"
- Required for HLS M4S recorder
- Optional for other recorders

**buffering**: Buffering segment count
- Number of segments to download before playback
- Default: 5
- Adjusts for VOD streams with few segments

**socketserver**: Socket server instance
- For sending progress and status messages
- Required for client communication
- Used by all recorders

---

## Socket Server Messages

### Purpose
Communicate recording status and progress to client applications.

### Message Format

```python
[command: str, data: dict]
```

### Start Message

**Sent**: When buffering complete or first chunk downloaded

**Format**:
```python
[
    "start",
    {
        "url": str,                    # Video URL
        "rec_file": str,               # Recording file path
        "section_index": int,          # Section index (0 for MP4)
        "segment_index": int,          # Segment index (0 for MP4)
        "recorder": {
            "type": str                # "mp4", "hls_basic", etc.
        }
    }
]
```

**Purpose**: Signal client that playback can begin

### Stop Message

**Sent**: When recording completes, fails, or is stopped

**Format**:
```python
[
    "stop",
    {
        "reason": str,                 # "complete", "error", "user"
        "error_id": str | None,        # "failure", "drm_protected", "timeout"
        "msg": str | None,             # Error message
        "channel": str | None,         # Channel URI
        "rec_dir": str | None,         # Recording directory
        "recorder": {
            "type": str                # Recorder type
        }
    }
]
```

**Reasons**:
- `"complete"`: Recording finished successfully (VOD)
- `"error"`: Recording failed (see error_id)
- `"user"`: User stopped recording

**Error IDs**:
- `"failure"`: General error
- `"drm_protected"`: DRM protection detected
- `"timeout"`: Connection timeout
- `"forbidden"`: 403 Forbidden (auth failure)

---

## Threading Model

### Thread Hierarchy

```
Main Thread (Socket Server)
└── Recorder Thread (BaseRecorder.start_thread)
    ├── MP4 Download (blocking I/O in thread)
    ├── HLS Loop (blocking I/O in thread)
    └── FFmpeg Monitor (polling in thread)
```

### Thread Safety

**Single Recorder Rule**: Only one recorder runs at a time
- Enforced by Recorder manager
- Previous recorder stopped before starting new

**Stop Event**: `threading.Event` for graceful shutdown
- Checked in all loops
- Set by `stop()` method
- Enables clean thread exit

**Thread Join**: `thread.join(timeout=5)` ensures completion
- Blocks until thread finishes or timeout
- Prevents orphaned threads
- Guarantees cleanup

### Lifecycle

```
1. Recorder.start_recorder()
   ↓
2. recorder_instance.start_thread(resolve_result)
   ↓
3. Thread created with _thread_wrapper
   ↓
4. Thread calls record_start(resolve_result)
   ↓
5. record_start() calls record_stream()
   ↓
6. record_stream() loops until stop_event.is_set()
   ↓
7. stop_event set by stop() or error
   ↓
8. Thread exits record_stream()
   ↓
9. _thread_wrapper catches exceptions
   ↓
10. _thread_wrapper calls on_thread_ended()
    ↓
11. is_running = False, thread terminates
```

---

## Error Handling

### Error Types

**Network Errors**:
- Connection timeout
- HTTP errors (403, 404, 500)
- DNS resolution failure

**Content Errors**:
- Invalid TS segments
- DRM protection
- Encrypted content without keys

**Process Errors**:
- FFmpeg crashes
- Disk full
- Permission denied

### Error Propagation

1. **Exception in record_stream()**:
   ```python
   try:
       self.record_stream(url, rec_dir)
   except Exception as e:
       logger.error("Recording error: %s", e)
       # Exception caught by _thread_wrapper
   ```

2. **_thread_wrapper catches all**:
   ```python
   try:
       self.record_start(resolve_result)
   except Exception as e:
       self.on_thread_error(e, error_id="failure")
   finally:
       self.on_thread_ended()
   ```

3. **on_thread_error broadcasts**:
   ```python
   if self.socketserver and not self.stop_sent:
       self.socketserver.broadcast([
           "stop",
           {"reason": "error", "error_id": error_id, "msg": str(error)}
       ])
   ```

### DRM Detection

**Multiple Check Points**:

1. **Playlist parsing**:
   ```python
   if detect_drm_in_content(playlist_text, content_type="m3u8")["has_drm"]:
       raise ValueError("DRM_PROTECTED: Detected in playlist")
   ```

2. **Segment download**:
   ```python
   drm_result = comprehensive_drm_check(
       url=segment_url,
       content=segment_data,
       content_type="ts"
   )
   if drm_result["has_drm"]:
       raise ValueError(f"DRM_PROTECTED: {drm_result['details']}")
   ```

3. **Error handling**:
   ```python
   except Exception as e:
       error_str = str(e)
       if error_str.startswith("DRM_PROTECTED:"):
           error_id = "drm_protected"
       else:
           error_id = "failure"
   ```

### Retry Logic

**Playlist Fetch**:
- Retry on failure
- Max 5 failed attempts
- Reload master playlist on repeated failures

**Segment Download**:
- Max 10 retries per segment
- Exponential backoff
- Skip segment after max retries

**FFmpeg Errors**:
- No automatic retry
- Log stderr for diagnosis
- Broadcast error message

---

## Best Practices

### Do's

1. **Always call super().record_start()**:
   ```python
   def record_start(self, resolve_result):
       super().record_start(resolve_result)  # Cleanup
       # Your implementation
   ```

2. **Check stop_event in loops**:
   ```python
   while not self.stop_event.is_set():
       # Recording logic
   ```

3. **Use session from resolver**:
   ```python
   self.session = resolve_result.get("session")
   if not self.session:
       self.session = requests.Session()  # Fallback
   ```

4. **Call start_playback after buffering**:
   ```python
   if chunk_count == 1:  # MP4
       self.start_playback(url, output_file)
   
   if segment_index >= buffering:  # HLS
       self.start_playback(url, output_file)
   ```

5. **Clean up resources in finally block**:
   ```python
   finally:
       if self.ffmpeg_process:
           terminate_ffmpeg_process(self.ffmpeg_process)
   ```

### Don'ts

1. **Don't create new sessions**:
   ```python
   # BAD: Loses authentication
   self.session = requests.Session()
   
   # GOOD: Reuse from resolver
   self.session = resolve_result.get("session")
   ```

2. **Don't ignore stop_event**:
   ```python
   # BAD: Infinite loop
   while True:
       # Recording
   
   # GOOD: Check stop
   while not self.stop_event.is_set():
       # Recording
   ```

3. **Don't block indefinitely**:
   ```python
   # BAD: No timeout
   response = session.get(url)
   
   # GOOD: With timeout
   response = session.get(url, timeout=60)
   ```

4. **Don't hardcode output paths**:
   ```python
   # BAD: Hardcoded path
   output_file = "/tmp/stream_0.mp4"
   
   # GOOD: Use rec_dir
   output_file = os.path.join(self.rec_dir, "stream_0.mp4")
   ```

5. **Don't suppress errors silently**:
   ```python
   # BAD: Silent failure
   try:
       segment_data = download_segment()
   except:
       pass
   
   # GOOD: Log and handle
   try:
       segment_data = download_segment()
   except Exception as e:
       logger.error("Download failed: %s", e)
       return None
   ```

---

## Design Decisions

### Why threading instead of multiprocessing?

**Reason**: Shared state and I/O focus
- Recorders are I/O-bound (network, disk)
- Share socket server for messaging
- Simpler than multiprocessing IPC
- Lower overhead

### Why single active recorder?

**Reason**: Resource management
- Prevents bandwidth saturation
- Avoids disk I/O conflicts
- Simplifies state management
- Clear user expectations

### Why pass full resolve_result dict?

**Reason**: Extensibility
- Future parameters added without API changes
- Recorders extract what they need
- Unused fields ignored
- Backward compatible

### Why separate HLS recorders (Basic/Live/M4S)?

**Reason**: Different strategies
- Basic: VOD, processes all segments once
- Live: Continuous, tracks last_sequence
- M4S: Delegates to FFmpeg, different format
- Separation avoids complex conditionals

### Why use BaseRecorder for threading?

**Reason**: Consistent lifecycle
- All recorders need threading
- Common cleanup logic
- Guaranteed stop behavior
- Reduces code duplication

### Why pass session from resolver?

**Reason**: Authentication reuse
- Resolver already authenticated
- Session contains cookies and headers
- Avoids re-authentication overhead
- Trusted architecture pattern

### Why FFmpeg for M4S?

**Reason**: Format complexity
- M4S requires MP4 parsing
- FFmpeg handles HLS variants
- Transcoding needed for compatibility
- Mature and tested

---

## Future Enhancements

### Potential Improvements

1. **Progress Events**:
   - Periodic progress messages to client
   - Bandwidth and ETA calculations
   - Segment completion notifications

2. **Quality Switching**:
   - Adaptive bitrate selection
   - Bandwidth monitoring
   - Quality degradation on slow connections

3. **Parallel Segment Downloads**:
   - Download multiple segments concurrently
   - Faster buffering for HLS
   - Queue management

4. **Resume Support**:
   - Detect partial downloads
   - Resume from last segment
   - Checkpointing

5. **Error Recovery**:
   - Automatic retry with backoff
   - Fallback quality selection
   - Alternative CDN selection

6. **Metrics Collection**:
   - Download speed tracking
   - Error rate monitoring
   - Performance analytics

---

## Summary

The recorder system handles video downloads through:

1. **BaseRecorder**: Common threading and lifecycle management
2. **MP4_Recorder**: Direct HTTP downloads with progress tracking
3. **HLS_Recorder_Basic**: Standard HLS VOD with TS segments
4. **HLS_Recorder_Live**: Live HLS with continuous playlist updates
5. **HLS_Recorder_M4S**: HLS with FFmpeg transcoding for M4S segments
6. **Recorder Manager**: Single active recorder orchestration

**Key Principles**:
- Session reuse for authenticated downloads
- Resolver specifies recorder type
- Thread-safe with single recorder rule
- Graceful shutdown via stop_event
- Progress feedback via socket server
- DRM detection at multiple levels
- Clean resource management

**Data Flow**:
1. Resolver → resolve_result with session
2. Recorder Manager → select recorder type
3. Recorder → extract session and URL
4. Download → use authenticated session
5. Progress → broadcast via socket server
6. Completion → cleanup and stop message
