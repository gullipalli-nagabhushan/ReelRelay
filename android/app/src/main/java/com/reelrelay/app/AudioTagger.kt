package com.reelrelay.app

import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Locale

/**
 * Writes tags and cover art straight into MP3 and FLAC files.
 *
 * The bundled ffmpeg converts audio, but its metadata step depends on the
 * device build and can silently drop the picture. MP3 (ID3v2.3 `APIC`) and FLAC
 * (`PICTURE` + Vorbis comments) are simple enough to write here, so the cover
 * is guaranteed to be present and readable by every phone player.
 *
 * Everything is copied; only the metadata region is rebuilt, so the audio
 * frames stay byte-for-byte identical.
 */
object AudioTagger {

    /** Containers this writer handles; everything else keeps ffmpeg metadata. */
    val CONTAINERS = setOf("mp3", "flac", "m4a")

    fun supports(extension: String): Boolean =
        extension.lowercase(Locale.US) in CONTAINERS

    /**
     * Rewrites [file] in place with [meta]'s tags and, when supplied, [cover]
     * (a JPEG). Returns `true` when the metadata region was rewritten.
     *
     * No re-encoding happens, so the audio frames are never touched. A `null`
     * cover still updates the text tags: this is the fallback that guarantees
     * readable tags even when the bundled ffmpeg cannot run.
     */
    fun embed(file: File, meta: AudioMeta, cover: File?): Boolean = runCatching {
        val coverBytes = cover?.takeIf { it.isFile && it.length() > 0L }?.readBytes()
        when (file.extension.lowercase(Locale.US)) {
            "mp3" -> rewriteMp3(file, meta, coverBytes)
            "flac" -> rewriteFlac(file, meta, coverBytes)
            "m4a" -> Mp4Artwork.embed(file, meta, coverBytes)
            else -> false
        }
    }.getOrDefault(false)

    /** True when [file] already carries an embedded picture. */
    fun hasCover(file: File): Boolean = runCatching {
        val bytes = file.readBytes()
        when (file.extension.lowercase(Locale.US)) {
            "mp3" -> mp3HasPicture(bytes)
            "flac" -> flacHasPicture(bytes)
            "m4a" -> Mp4Artwork.hasCover(file)
            else -> false
        }
    }.getOrDefault(false)

    // ---------------------------------------------------------------- MP3 --

    private fun rewriteMp3(file: File, meta: AudioMeta, cover: ByteArray?): Boolean {
        val bytes = file.readBytes()
        val start = id3Start(bytes)
        val end = if (bytes.size >= 128 && tagAt(bytes, bytes.size - 128)) {
            bytes.size - 128
        } else {
            bytes.size
        }
        if (start >= end) return false

        val frames = ArrayList<ByteArray>()
        frames += textFrame("TIT2", meta.title)
        frames += textFrame("TPE1", meta.artist)
        frames += textFrame("TPE2", meta.artist)
        frames += commentFrame("COMM", "Source: ${meta.sourceUrl}")
        if (cover != null && cover.isNotEmpty()) frames += pictureFrame(cover)
        // Same frame the desktop writer uses, so every player shows
        // "encoder = ReelRelay" and the file is recognisably ours.
        frames += textFrame("TSSE", "ReelRelay")

        val payloadSize = frames.sumOf { it.size }
        val header = ByteArray(10)
        header[0] = 'I'.code.toByte()
        header[1] = 'D'.code.toByte()
        header[2] = '3'.code.toByte()
        header[3] = 3 // ID3v2.3
        header[4] = 0
        header[5] = 0
        writeSyncSafe(header, 6, payloadSize)

        val temp = File(file.parentFile, "${file.name}.tagtmp")
        temp.outputStream().use { out ->
            out.write(header)
            frames.forEach(out::write)
            out.write(bytes, start, end - start)
        }
        return replace(file, temp)
    }

    private fun id3Start(bytes: ByteArray): Int {
        if (bytes.size < 10 || !tagAt(bytes, 0)) return 0
        val size = readSyncSafe(bytes, 6)
        val footer = if (bytes[5].toInt() and 0x10 != 0) 10 else 0
        return (10 + size + footer).coerceIn(0, bytes.size)
    }

    private fun mp3HasPicture(bytes: ByteArray): Boolean {
        val start = id3Start(bytes)
        if (start <= 10) return false
        val tag = String(bytes, 0, minOf(start, 4096), Charsets.ISO_8859_1)
        return tag.contains("APIC")
    }

    private fun textFrame(id: String, value: String): ByteArray {
        val cleaned = cleanTag(value)
        if (cleaned.isEmpty()) return ByteArray(0)
        return frame(id, byteArrayOf(1) + utf16(cleaned))
    }

    private fun commentFrame(id: String, value: String): ByteArray {
        val cleaned = cleanTag(value, 300)
        if (cleaned.isEmpty()) return ByteArray(0)
        val payload = byteArrayOf(1) + "eng".toByteArray(Charsets.US_ASCII) +
            byteArrayOf(0xFF.toByte(), 0xFE.toByte(), 0, 0) +
            utf16(cleaned)
        return frame(id, payload)
    }

    private fun pictureFrame(cover: ByteArray): ByteArray {
        val mime = "image/jpeg".toByteArray(Charsets.US_ASCII)
        val payload = ByteArray(1 + mime.size + 1 + 1 + 1 + cover.size)
        var index = 0
        payload[index++] = 0 // ISO-8859-1 description
        mime.copyInto(payload, index)
        index += mime.size
        payload[index++] = 0 // mime terminator
        payload[index++] = 3 // front cover
        payload[index++] = 0 // empty description
        cover.copyInto(payload, index)
        return frame("APIC", payload)
    }

    private fun frame(id: String, payload: ByteArray): ByteArray {
        if (payload.isEmpty()) return ByteArray(0)
        val out = ByteArray(10 + payload.size)
        id.toByteArray(Charsets.US_ASCII).copyInto(out, 0)
        ByteBuffer.wrap(out, 4, 4).order(ByteOrder.BIG_ENDIAN).putInt(payload.size)
        payload.copyInto(out, 10)
        return out
    }

    private fun utf16(value: String): ByteArray {
        val body = value.toByteArray(Charsets.UTF_16LE)
        val out = ByteArray(body.size + 2)
        out[0] = 0xFF.toByte()
        out[1] = 0xFE.toByte()
        body.copyInto(out, 2)
        return out
    }

    // --------------------------------------------------------------- FLAC --

    private fun rewriteFlac(file: File, meta: AudioMeta, cover: ByteArray?): Boolean {
        val bytes = file.readBytes()
        if (bytes.size < 8 || !flacAt(bytes)) return false

        val blocks = ArrayList<Pair<Int, ByteArray>>()
        var offset = 4
        var audioStart = bytes.size
        while (offset + 4 <= bytes.size) {
            val type = bytes[offset].toInt() and 0x7F
            val last = bytes[offset].toInt() and 0x80 != 0
            val length = ((bytes[offset + 1].toInt() and 0xFF) shl 16) or
                ((bytes[offset + 2].toInt() and 0xFF) shl 8) or
                (bytes[offset + 3].toInt() and 0xFF)
            val bodyStart = offset + 4
            val bodyEnd = bodyStart + length
            if (bodyEnd > bytes.size) return false
            // Drop comments, pictures and padding: they are rebuilt below.
            if (type != 4 && type != 6 && type != 1) {
                blocks += type to bytes.copyOfRange(bodyStart, bodyEnd)
            }
            offset = bodyEnd
            if (last) {
                audioStart = offset
                break
            }
        }

        val rebuilt = ArrayList<Pair<Int, ByteArray>>()
        var streamInfo: Pair<Int, ByteArray>? = null
        for (block in blocks) {
            if (block.first == 0 && streamInfo == null) streamInfo = block else rebuilt += block
        }
        streamInfo?.let { rebuilt.add(0, it) }
        rebuilt += 4 to vorbisComment(meta)
        if (cover != null && cover.isNotEmpty()) rebuilt += 6 to pictureBlock(cover)

        val temp = File(file.parentFile, "${file.name}.tagtmp")
        temp.outputStream().use { out ->
            out.write(bytes, 0, 4)
            rebuilt.forEachIndexed { index, (type, body) ->
                val last = index == rebuilt.lastIndex
                out.write(if (last) type or 0x80 else type)
                out.write((body.size shr 16) and 0xFF)
                out.write((body.size shr 8) and 0xFF)
                out.write(body.size and 0xFF)
                out.write(body)
            }
            out.write(bytes, audioStart, bytes.size - audioStart)
        }
        return replace(file, temp)
    }

    private fun flacHasPicture(bytes: ByteArray): Boolean {
        var offset = 4
        while (offset + 4 <= bytes.size) {
            val type = bytes[offset].toInt() and 0x7F
            val last = bytes[offset].toInt() and 0x80 != 0
            val length = ((bytes[offset + 1].toInt() and 0xFF) shl 16) or
                ((bytes[offset + 2].toInt() and 0xFF) shl 8) or
                (bytes[offset + 3].toInt() and 0xFF)
            if (type == 6) return true
            offset += 4 + length
            if (last) break
        }
        return false
    }

    private fun vorbisComment(meta: AudioMeta): ByteArray {
        val vendor = "ReelRelay".toByteArray(Charsets.UTF_8)
        val fields = ArrayList<String>()
        cleanTag(meta.title).takeIf { it.isNotEmpty() }?.let { fields += "TITLE=$it" }
        cleanTag(meta.artist).takeIf { it.isNotEmpty() }?.let {
            fields += "ARTIST=$it"
            fields += "ALBUMARTIST=$it"
        }
        cleanTag("Source: ${meta.sourceUrl}", 300).takeIf { it.isNotEmpty() }?.let {
            fields += "COMMENT=$it"
        }
        fields += "ENCODER=ReelRelay"

        val size = 4 + vendor.size + 4 + fields.sumOf { 4 + it.toByteArray(Charsets.UTF_8).size }
        val buffer = ByteBuffer.allocate(size).order(ByteOrder.LITTLE_ENDIAN)
        buffer.putInt(vendor.size)
        buffer.put(vendor)
        buffer.putInt(fields.size)
        for (field in fields) {
            val encoded = field.toByteArray(Charsets.UTF_8)
            buffer.putInt(encoded.size)
            buffer.put(encoded)
        }
        return buffer.array()
    }

    private fun pictureBlock(cover: ByteArray): ByteArray {
        val mime = "image/jpeg".toByteArray(Charsets.US_ASCII)
        val (width, height) = jpegSize(cover)
        val buffer = ByteBuffer.allocate(
            4 + 4 + mime.size + 4 + 16 + 4 + cover.size,
        ).order(ByteOrder.BIG_ENDIAN)
        buffer.putInt(3) // front cover
        buffer.putInt(mime.size)
        buffer.put(mime)
        buffer.putInt(0) // empty description
        buffer.putInt(width)
        buffer.putInt(height)
        buffer.putInt(24) // colour depth
        buffer.putInt(0) // indexed colours
        buffer.putInt(cover.size)
        buffer.put(cover)
        return buffer.array()
    }

    /** Reads the pixel size from a JPEG's start-of-frame marker. */
    internal fun jpegSize(cover: ByteArray): Pair<Int, Int> {
        var index = 2
        while (index + 9 < cover.size) {
            if (cover[index].toInt() and 0xFF != 0xFF) {
                index++
                continue
            }
            val marker = cover[index + 1].toInt() and 0xFF
            if (marker == 0xD8 || marker == 0xD9 || marker == 0x01 ||
                marker in 0xD0..0xD7
            ) {
                index += 2
                continue
            }
            val length = ((cover[index + 2].toInt() and 0xFF) shl 8) or
                (cover[index + 3].toInt() and 0xFF)
            if (length < 2) return 0 to 0
            val isFrame = marker in 0xC0..0xC3 || marker in 0xC5..0xC7 ||
                marker in 0xC9..0xCB || marker in 0xCD..0xCF
            if (isFrame) {
                val height = ((cover[index + 5].toInt() and 0xFF) shl 8) or
                    (cover[index + 6].toInt() and 0xFF)
                val width = ((cover[index + 7].toInt() and 0xFF) shl 8) or
                    (cover[index + 8].toInt() and 0xFF)
                return width to height
            }
            index += 2 + length
        }
        return 0 to 0
    }

    // -------------------------------------------------------------- shared --

    private fun flacAt(bytes: ByteArray): Boolean =
        bytes.size >= 4 &&
            bytes[0] == 'f'.code.toByte() &&
            bytes[1] == 'L'.code.toByte() &&
            bytes[2] == 'a'.code.toByte() &&
            bytes[3] == 'C'.code.toByte()

    private fun tagAt(bytes: ByteArray, offset: Int): Boolean =
        bytes.size >= offset + 3 &&
            bytes[offset] == 'I'.code.toByte() &&
            bytes[offset + 1] == 'D'.code.toByte() &&
            bytes[offset + 2] == '3'.code.toByte()

    private fun readSyncSafe(bytes: ByteArray, offset: Int): Int =
        ((bytes[offset].toInt() and 0x7F) shl 21) or
            ((bytes[offset + 1].toInt() and 0x7F) shl 14) or
            ((bytes[offset + 2].toInt() and 0x7F) shl 7) or
            (bytes[offset + 3].toInt() and 0x7F)

    private fun writeSyncSafe(bytes: ByteArray, offset: Int, value: Int) {
        bytes[offset] = ((value shr 21) and 0x7F).toByte()
        bytes[offset + 1] = ((value shr 14) and 0x7F).toByte()
        bytes[offset + 2] = ((value shr 7) and 0x7F).toByte()
        bytes[offset + 3] = (value and 0x7F).toByte()
    }

    private fun replace(target: File, temp: File): Boolean {
        if (!temp.isFile || temp.length() <= 0L) {
            temp.delete()
            return false
        }
        if (!temp.renameTo(target)) {
            temp.copyTo(target, overwrite = true)
            temp.delete()
        }
        return target.isFile && target.length() > 0L
    }
}
