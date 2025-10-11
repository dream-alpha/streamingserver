# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Cryptographic Utilities for HLS Stream Processing

This module provides helper functions for handling encrypted HLS streams,
specifically those using AES-128 encryption. It includes functions for
downloading encryption keys and decrypting media segments.
"""
import traceback
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from debug import get_logger

logger = get_logger(__file__)


def get_encryption_info(session, encryption_info):
    """
    Parses encryption information from the playlist and fetches the key if necessary.

    This function processes the encryption details for a given segment. If a key
    URI is provided, it attempts to download the key. It also processes the
    Initialization Vector (IV) if present.

    Args:
        session (requests.Session): The session object for making HTTP requests.
        encryption_info (dict): A dictionary containing encryption details from
                                the HLS playlist (e.g., METHOD, URI, IV).

    Returns:
        dict: A dictionary containing the processed encryption key, method, and IV.
              Returns a dictionary with None values if key fetching fails.
    """
    # Use segment-specific encryption info if available, otherwise use global

    segment_key = {
        "METHOD": None,
        "KEY": None,
        "IV": None,
        "URI": None
    }

    if encryption_info.get("METHOD"):
        segment_key["METHOD"] = encryption_info.get("METHOD")

        # Download the encryption key if URI is provided
        if encryption_info.get("URI") and encryption_info.get("METHOD") == "AES-128":
            try:
                segment_key["KEY"] = download_encryption_key(session, encryption_info["URI"])
                # Process IV: convert from hex string to bytes if present
                key_iv = encryption_info.get("IV")
                if key_iv:
                    try:
                        segment_key["IV"] = bytes.fromhex(key_iv.replace("0x", ""))
                        logger.debug("Processed IV for segment: %s bytes", len(segment_key['IV']))
                    except Exception as e:
                        logger.error("Error converting IV to bytes: %s", e)
                        segment_key["IV"] = None

            except Exception as e:
                logger.error("Error fetching AES key: %s", e)
                segment_key["KEY"] = None
    return segment_key


def download_encryption_key(session, key_url):
    """
    Downloads the AES-128 encryption key from a given URL.

    Args:
        session (requests.Session): The session object for making HTTP requests.
        key_url (str): The URL from which to download the encryption key.

    Returns:
        bytes or None: The encryption key as bytes if successful (should be 16 bytes),
                       or None if the download fails.
    """
    try:
        # logger.debug("Downloading encryption key from: %s", key_url)
        response = session.get(key_url, allow_redirects=True, timeout=10)
        response.raise_for_status()

        key_data = response.content
        # logger.debug(f"Downloaded key: {len(key_data)} bytes")

        # AES-128 keys should be exactly 16 bytes
        if len(key_data) == 16:
            return key_data
        logger.error("Unexpected key length: %s bytes (expected 16)", len(key_data))
        return key_data  # Return anyway, might still work

    except Exception as e:
        logger.error("Error downloading encryption key: %s", e)
        return None


def decrypt_segment(encrypted_data, segment_sequence, media_sequence_base, current_key):
    """
    Decrypts an encrypted HLS segment using AES-128.

    This function handles the decryption of a single media segment. It determines
    the correct IV to use (either from the playlist or derived from the segment's
    sequence number) and then performs AES-128 CBC decryption and PKCS7 unpadding.

    Args:
        encrypted_data (bytes): The raw, encrypted segment data.
        segment_sequence (int): The sequence number of the segment.
        media_sequence_base (int or None): The base media sequence number from the playlist.
        current_key (dict): A dictionary containing the 'KEY', 'METHOD', and 'IV'.

    Returns:
        bytes or None: The decrypted segment data as bytes, or None if decryption fails
                       (e.g., due to incorrect key, padding errors, or other exceptions).
    """
    if not current_key["KEY"] or current_key["METHOD"] != 'AES-128':
        # No encryption or unsupported method
        logger.debug("No encryption key or unsupported method, returning encrypted data as is")
        return None

    try:
        # Determine IV
        if current_key["IV"]:
            iv = current_key["IV"]
            # iv_source = "playlist"
        else:
            # Use EXT-X-MEDIA-SEQUENCE as base if provided
            seq = segment_sequence
            if media_sequence_base is not None:
                seq = media_sequence_base + segment_sequence
            iv = seq.to_bytes(16, byteorder='big')
            # iv_source = f'seq={seq}'

        # logger.debug(f"Decrypting segment (segment_sequence: {segment_sequence}, IV source: {iv_source})")
        # logger.debug(f"Key: {current_key.hex()} IV: {iv.hex()}")

        # Create AES cipher
        cipher = AES.new(current_key["KEY"], AES.MODE_CBC, iv)
        decrypted_data = cipher.decrypt(encrypted_data)
        # Unpad after decryption (PKCS7, AES block size = 16)
        try:
            decrypted_data = unpad(decrypted_data, 16)
        except Exception as e:
            logger.error("Unpadding failed for segment %s: %s", segment_sequence, e)
            return None
        # Log first 32 bytes of decrypted data for debug
        # logger.debug(f"[DECRYPT DEBUG] First 32 bytes: {decrypted_data[:32].hex()}")

        return decrypted_data

    except Exception as e:
        logger.error("Error decrypting segment: %s", e)
        logger.error("   Key: %s", current_key.hex() if current_key else None)
        logger.error("   IV: %s (seq: %s)", current_key["IV"].hex() if current_key["IV"] else 'derived', segment_sequence)
        traceback.print_exc()
        # Do NOT return encrypted data, skip segment instead
        return None
