# ISO Base Media File Format (MP4 Structure)

The ISO Base Media File Format (ISO/IEC 14496-12) is the foundation for several multimedia file formats, including MP4, MOV, 3GP, and others. Here's an overview of the key components:

## Basic Box Structure

All data in ISO BMFF files is organized in "boxes" (also known as "atoms" in QuickTime). Each box has:

- **Size**: 4-byte field indicating the box's total size (including header)
- **Type**: 4-byte field identifying the box type (e.g., 'ftyp', 'moov', 'mdat')

```
+---------------+----------------+-------------------+
| Size (4 bytes)| Type (4 bytes) | Box Data (n bytes)|
+---------------+----------------+-------------------+
```

## Full Box Structure

Many boxes are "full boxes" that add version and flags information:

```
+---------------+----------------+------------------+------------------+-------------------+
| Size (4 bytes)| Type (4 bytes) | Version (1 byte) | Flags (3 bytes) | Box Data (n bytes)|
+---------------+----------------+------------------+------------------+-------------------+
```

## Key Boxes in Standard MP4

### Top-Level Box Structure
```
- ftyp (File Type Box)
- moov (Movie Box)
  - mvhd (Movie Header Box)
  - trak (Track Box) [one per track]
    - tkhd (Track Header Box)
    - mdia (Media Box)
      - mdhd (Media Header Box)
      - hdlr (Handler Box)
      - minf (Media Information Box)
        - stbl (Sample Table Box)
          - stsd (Sample Description Box)
          - stts (Time-to-Sample Box)
          - stsc (Sample-to-Chunk Box)
          - stsz (Sample Size Box)
          - stco (Chunk Offset Box)
- mdat (Media Data Box)
```

## Key Boxes in Fragmented MP4 (Used in Streaming)

### Initialization Segment Structure
```
- ftyp (File Type Box)
- moov (Movie Box)
  - mvhd (Movie Header Box)
  - trak (Track Box)
    - tkhd (Track Header Box)
    - mdia (Media Box)
      - [...]
  - mvex (Movie Extends Box)
    - trex (Track Extends Box)
```

### Media Segment Structure
```
- styp (Segment Type Box, optional)
- moof (Movie Fragment Box)
  - mfhd (Movie Fragment Header Box)
  - traf (Track Fragment Box)
    - tfhd (Track Fragment Header Box)
    - tfdt (Track Fragment Decode Time Box)
    - trun (Track Fragment Run Box)
- mdat (Media Data Box)
```

## Important Boxes for Streaming

### ftyp (File Type Box)
Identifies file compatibility and specifications.

**Properties:**
- Major Brand: Main specification (e.g., 'iso6', 'dash')
- Minor Version: Version of major brand
- Compatible Brands: Array of other compatible specifications

### moov (Movie Box)
Contains metadata about the entire presentation.

### moof (Movie Fragment Box)
Contains metadata for a fragment of media (used in streaming).

### mfhd (Movie Fragment Header Box)
Contains sequence number to identify the fragment.

### traf (Track Fragment Box)
Contains track-specific fragment information.

### tfhd (Track Fragment Header Box)
Contains default values for the track fragment.

**Flags:**
- base-data-offset-present: Indicates if baseDataOffset field exists
- default-base-is-moof: Indicates if fragment base offset is relative to moof start

### trun (Track Fragment Run Box)
Contains timing and positioning information for frames/samples in a fragment.

### mdat (Media Data Box)
Contains the actual media data (video/audio frames).

### tfdt (Track Fragment Decode Time Box)
Provides the absolute decode time for the first sample in a fragment, essential for proper fragment sequencing.

## Significance for Streaming

In adaptive streaming formats like DASH and HLS:

1. The **initialization segment** contains the 'moov' box with codec information but no media data
2. The **media segments** contain 'moof' + 'mdat' pairs with actual media data

This separation allows players to:
- Initialize decoders once with the init segment
- Process individual media segments independently 
- Switch between quality levels by requesting different media segments

For processing fragmented MP4s properly, you need to:
1. Parse and process the initialization segment first
2. Process each media segment in sequence
3. Use the tfdt box to ensure proper timeline alignment