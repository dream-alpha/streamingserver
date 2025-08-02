import traceback
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad


def get_encryption_info(session, segment_encryption_info, global_encryption_info):
    # Use segment-specific encryption info if available, otherwise use global
    encryption_info = segment_encryption_info if segment_encryption_info else global_encryption_info

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
                        print(f"🔐 Processed IV for segment: {len(segment_key['IV'])} bytes")
                    except Exception as e:
                        print(f"❌ Error converting IV to bytes: {e}")
                        segment_key["IV"] = None

            except Exception as e:
                print(f"❌ Error fetching AES key: {e}")
                segment_key["KEY"] = None
    return segment_key


def download_encryption_key(session, key_url):
    """Download the encryption key from the given URL"""
    try:
        # print(f"🔑 Downloading encryption key from: {key_url}")
        response = session.get(key_url, allow_redirects=True, timeout=10)
        response.raise_for_status()

        key_data = response.content
        # print(f"🔑 Downloaded key: {len(key_data)} bytes")

        # AES-128 keys should be exactly 16 bytes
        if len(key_data) == 16:
            return key_data
        print(f"⚠ Unexpected key length: {len(key_data)} bytes (expected 16)")
        return key_data  # Return anyway, might still work

    except Exception as e:
        print(f"❌ Error downloading encryption key: {e}")
        return None


def decrypt_segment(encrypted_data, segment_sequence, media_sequence_base, current_key):
    """Decrypt an encrypted HLS segment using AES-128 (no PKCS7 removal for TS)"""
    if not current_key["KEY"] or current_key["METHOD"] != 'AES-128':
        # No encryption or unsupported method
        print("📝 No encryption key or unsupported method, returning encrypted data as is")
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

        # print(f"🔓 Decrypting segment (segment_sequence: {segment_sequence}, IV source: {iv_source})")
        # print(f"🔓 Key: {current_key.hex()} IV: {iv.hex()}")

        # Create AES cipher
        cipher = AES.new(current_key["KEY"], AES.MODE_CBC, iv)
        decrypted_data = cipher.decrypt(encrypted_data)
        # Unpad after decryption (PKCS7, AES block size = 16)
        try:
            decrypted_data = unpad(decrypted_data, 16)
        except Exception as e:
            print(f"❌ Unpadding failed for segment {segment_sequence}: {e}")
            return None
        # Log first 32 bytes of decrypted data for debug
        # print(f"[DECRYPT DEBUG] First 32 bytes: {decrypted_data[:32].hex()}")

        return decrypted_data

    except Exception as e:
        print(f"❌ Error decrypting segment: {e}")
        print(f"   Key: {current_key.hex() if current_key else None}")
        print(f"   IV: {current_key["IV"].hex() if current_key["IV"] else 'derived'} (seq: {segment_sequence})")
        traceback.print_exc()
        # Do NOT return encrypted data, skip segment instead
        return None
