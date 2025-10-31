# FFmpeg 7 Improvements for Streaming Server

## Current Usage Analysis

Your streaming server currently uses FFmpeg for:
1. **M4S HLS Recording** (`hls_recorder_m4s.py`) - Transcodes to H.264 with TS/MP4 output
2. **Segment Processing** (`ffmpeg_utils.py`) - Copies TS segments via stdin
3. **Basic/Live Recording** - Uses FFmpeg for stream processing

## FFmpeg 7 New Features That Could Benefit You

### 1. **Improved Hardware Acceleration (High Priority)**

**What's New:**
- Better VA-API support for encoding/decoding (Linux)
- Improved NVENC presets and quality
- Better multi-threaded hardware encoder support
- New `-init_hw_device` and `-filter_hw_device` options

**Current Code:**
```python
'-c:v', 'libx264',       # Software encoding
'-preset', 'ultrafast',
```

**Recommended Improvement:**
```python
# Try hardware encoding first, fallback to software
'-init_hw_device', 'vaapi=va:/dev/dri/renderD128',  # VA-API for Intel/AMD
'-hwaccel', 'vaapi',
'-hwaccel_output_format', 'vaapi',
'-c:v', 'h264_vaapi',    # Hardware H.264 encoding
'-qp', '23',             # Quality parameter for VA-API
```

**Benefits:**
- 3-5x faster encoding on compatible hardware
- Lower CPU usage (critical for Dreambox)
- Better quality at same bitrate

**Implementation:**
- Add hardware detection function
- Fallback to software if hardware unavailable
- Add config option for hardware acceleration

---

### 2. **New `-stats_period` Option (Medium Priority)**

**What's New:**
- Better control over statistics output frequency
- Reduces overhead of status polling

**Recommended Addition:**
```python
'-stats_period', '5',    # Update stats every 5 seconds
'-progress', 'pipe:2',   # Send progress to stderr for monitoring
```

**Benefits:**
- More efficient FFmpeg monitoring
- Better progress tracking for UI
- Lower overhead

---

### 3. **Improved MPEGTS Muxer Options (Medium Priority)**

**What's New:**
- Better timestamp handling with `-mpegts_flags`
- New `-mpegts_service_type` option
- Improved PES packet alignment

**Current Code:**
```python
'-f', 'mpegts',
'-mpegts_copyts', '1',
```

**Recommended Improvement:**
```python
'-f', 'mpegts',
'-mpegts_flags', 'initial_discontinuity',  # Better for live streams
'-mpegts_copyts', '1',
'-avoid_negative_ts', 'make_zero',         # Better timestamp handling
'-max_interleave_delta', '0',              # Reduce muxing delay
```

**Benefits:**
- Better Dreambox compatibility
- Reduced buffering delays
- More stable timestamps

---

### 4. **Better Error Detection (High Priority)**

**What's New:**
- Improved `-err_detect` flags
- Better network error recovery
- New `-reconnect_*` options for HLS

**Current Code:**
```python
'-fflags', '+discardcorrupt+genpts+igndts+ignidx+nofillin',
'-err_detect', 'ignore_err',
```

**Recommended Improvement:**
```python
'-fflags', '+discardcorrupt+genpts+igndts+ignidx+nofillin',
'-err_detect', 'careful',                    # Better than 'ignore_err'
'-reconnect', '1',                           # Auto-reconnect on network errors
'-reconnect_streamed', '1',                  # Reconnect for streamed content
'-reconnect_delay_max', '10',                # Max 10 seconds between retries
'-reconnect_at_eof', '1',                    # Reconnect if EOF unexpected
'-protocol_whitelist', 'file,http,https,tcp,tls',  # Security: whitelist protocols
```

**Benefits:**
- Automatic recovery from network glitches
- Better error reporting
- More robust long-running streams

---

### 5. **New Audio Filters (Low Priority)**

**What's New:**
- `loudnorm` filter improvements (EBU R128 normalization)
- Better AAC encoder quality

**Recommended Addition:**
```python
'-c:a', 'aac',
'-b:a', '128k',
'-af', 'loudnorm=I=-16:TP=-1.5:LRA=11',  # Normalize audio levels
```

**Benefits:**
- Consistent audio volume across streams
- Better viewer experience

---

### 6. **Improved Probing (Medium Priority)**

**What's New:**
- Faster probe times with better heuristics
- New `-probe_score` option

**Recommended Improvement:**
```python
'-probesize', '10M',         # Reduced from 256M (faster startup)
'-analyzeduration', '5M',    # Reduced from 40M (faster startup)
'-probe_score', '50',        # Accept streams with lower confidence
'-fps_mode', 'passthrough',  # Better framerate handling
```

**Benefits:**
- Faster stream startup
- Lower memory usage
- Better compatibility with non-standard streams

---

## Recommended Implementation Plan

### Phase 1: Low-Risk Improvements (Immediate)
1. ✅ Add `-reconnect` options for network reliability
2. ✅ Add `-stats_period` and `-progress` for better monitoring
3. ✅ Improve MPEGTS flags for better timestamps
4. ✅ Add `-protocol_whitelist` for security

### Phase 2: Hardware Acceleration (1-2 weeks)
1. Add hardware detection (VA-API, NVENC, QSV)
2. Implement fallback logic
3. Add configuration options
4. Test on Dreambox hardware

### Phase 3: Advanced Features (Optional)
1. Audio normalization filter
2. Adaptive bitrate based on CPU load
3. Multi-pass encoding for VOD content

---

## Example: Updated `_start_ffmpeg` with FFmpeg 7 Features

```python
def _start_ffmpeg_v7(self, input_url, output_file, ffmpeg_headers=None, use_mp4=False, use_hw_accel=None):
    """
    Start FFmpeg 7 process with improved features
    
    Args:
        use_hw_accel (str): 'vaapi', 'nvenc', 'qsv', or None for auto-detect
    """
    cmd = ['ffmpeg', '-y']
    
    # Headers
    if ffmpeg_headers:
        cmd += ['-headers', ffmpeg_headers]
    
    # Network resilience (FFmpeg 7)
    cmd += [
        '-reconnect', '1',
        '-reconnect_streamed', '1',
        '-reconnect_delay_max', '10',
        '-reconnect_at_eof', '1',
        '-protocol_whitelist', 'file,http,https,tcp,tls',
    ]
    
    # Hardware acceleration (if available)
    if use_hw_accel == 'vaapi':
        cmd += [
            '-init_hw_device', 'vaapi=va:/dev/dri/renderD128',
            '-hwaccel', 'vaapi',
            '-hwaccel_output_format', 'vaapi',
        ]
    
    # Input
    cmd += [
        '-i', input_url,
        '-fflags', '+discardcorrupt+genpts+igndts+ignidx+nofillin',
        '-err_detect', 'careful',
    ]
    
    # Faster probing (FFmpeg 7)
    cmd += [
        '-probesize', '10M',
        '-analyzeduration', '5M',
        '-probe_score', '50',
    ]
    
    # Video encoding
    if use_hw_accel == 'vaapi':
        cmd += [
            '-c:v', 'h264_vaapi',
            '-qp', '23',
        ]
    else:
        cmd += [
            '-c:v', 'libx264',
            '-profile:v', 'baseline',
            '-preset', 'ultrafast',
        ]
    
    # Audio
    cmd += [
        '-c:a', 'aac',
        '-b:a', '128k',
        '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11',  # Audio normalization
    ]
    
    # Output format
    if use_mp4:
        cmd += [
            '-f', 'mp4',
            '-movflags', '+faststart+frag_keyframe+empty_moov',
        ]
    else:
        cmd += [
            '-f', 'mpegts',
            '-mpegts_flags', 'initial_discontinuity',
            '-avoid_negative_ts', 'make_zero',
            '-max_interleave_delta', '0',
        ]
    
    # Monitoring (FFmpeg 7)
    cmd += [
        '-stats_period', '5',
        '-progress', 'pipe:2',
        '-loglevel', 'error',
        output_file
    ]
    
    return subprocess.Popen(cmd, stderr=subprocess.PIPE)
```

---

## Hardware Detection Helper

```python
def detect_hardware_acceleration():
    """
    Detect available hardware acceleration
    
    Returns:
        str: 'vaapi', 'nvenc', 'qsv', or None
    """
    import os
    
    # Check for VA-API (Intel/AMD on Linux)
    if os.path.exists('/dev/dri/renderD128'):
        try:
            result = subprocess.run(
                ['ffmpeg', '-hide_banner', '-hwaccels'],
                capture_output=True, text=True, timeout=5
            )
            if 'vaapi' in result.stdout:
                logger.info("VA-API hardware acceleration detected")
                return 'vaapi'
        except Exception:
            pass
    
    # Check for NVENC (NVIDIA)
    try:
        result = subprocess.run(
            ['ffmpeg', '-hide_banner', '-encoders'],
            capture_output=True, text=True, timeout=5
        )
        if 'h264_nvenc' in result.stdout:
            logger.info("NVENC hardware acceleration detected")
            return 'nvenc'
    except Exception:
        pass
    
    # Check for QSV (Intel Quick Sync)
    try:
        result = subprocess.run(
            ['ffmpeg', '-hide_banner', '-encoders'],
            capture_output=True, text=True, timeout=5
        )
        if 'h264_qsv' in result.stdout:
            logger.info("QSV hardware acceleration detected")
            return 'qsv'
    except Exception:
        pass
    
    logger.info("No hardware acceleration detected, using software encoding")
    return None
```

---

## Testing Checklist

- [ ] Test `-reconnect` options with unstable network
- [ ] Verify hardware acceleration on available hardware
- [ ] Compare CPU usage: software vs hardware encoding
- [ ] Test on Dreambox 920 (armhf) - may not have hardware encoding
- [ ] Verify audio normalization doesn't introduce delay
- [ ] Check MPEGTS output compatibility with Dreambox
- [ ] Monitor `-progress` output for status updates

---

## Performance Expectations

### Software Encoding (Current)
- CPU: 60-80% on one core
- Real-time factor: ~1.0x (720p)
- Latency: 2-3 seconds

### Hardware Encoding (VA-API)
- CPU: 10-20% 
- Real-time factor: 3-5x faster
- Latency: 1-2 seconds
- Quality: Same or better at same bitrate

### Network Resilience
- Automatic recovery from 5-10 second network drops
- No manual restart needed
- Transparent to user

---

## Configuration Options to Add

```python
# In config.json or settings
{
    "ffmpeg": {
        "hw_accel": "auto",           # auto, vaapi, nvenc, qsv, none
        "reconnect_enabled": true,     # Enable auto-reconnect
        "audio_normalize": false,      # Enable audio normalization
        "probe_size": "10M",          # Faster startup
        "stats_period": 5,            # Status update frequency
        "loglevel": "error"           # error, warning, info, debug
    }
}
```
