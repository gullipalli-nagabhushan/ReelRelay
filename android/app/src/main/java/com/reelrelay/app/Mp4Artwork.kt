package com.reelrelay.app

import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Writes iTunes-style metadata (including `covr` artwork) into an M4A file.
 *
 * Only the `udta` atom inside `moov` is rebuilt. When `moov` sits before
 * `mdat`, the media data shifts by the size difference, so every `stco`/`co64`
 * chunk offset is adjusted by the same amount. Audio samples themselves are
 * never touched.
 */
internal object Mp4Artwork {

    private val CONTAINERS = setOf(
        "moov", "trak", "mdia", "minf", "stbl", "edts", "dinf",
    )

    fun embed(file: File, meta: AudioMeta, cover: ByteArray?): Boolean = runCatching {
        embedOrFail(file, meta, cover)
    }.getOrDefault(false)

    private fun embedOrFail(file: File, meta: AudioMeta, cover: ByteArray?): Boolean {
        val bytes = file.readBytes()
        val top = boxes(bytes, 0, bytes.size) ?: return false
        val moov = top.firstOrNull { it.type == "moov" } ?: return false
        val mdat = top.firstOrNull { it.type == "mdat" } ?: return false
        // Fragmented files index samples in `moof`; leave those alone.
        if (top.any { it.type == "moof" }) return false

        val moovBody = bytes.copyOfRange(moov.start + 8, moov.end)
        val children = boxes(moovBody, 0, moovBody.size) ?: return false

        val ilst = box("ilst", buildMetadata(meta, cover))
        val existingUdta = children.firstOrNull { it.type == "udta" }
        val newUdta = if (existingUdta != null) {
            rebuildUdta(moovBody, existingUdta, ilst) ?: return false
        } else {
            box("udta", box("meta", fullBox() + handler() + ilst))
        }

        val rebuilt = ByteArray(moovBody.size - (existingUdta?.size ?: 0) + newUdta.size)
        var cursor = 0
        for (child in children) {
            if (child === existingUdta) {
                newUdta.copyInto(rebuilt, cursor)
                cursor += newUdta.size
            } else {
                moovBody.copyInto(rebuilt, cursor, child.start, child.end)
                cursor += child.size
            }
        }
        if (existingUdta == null) {
            newUdta.copyInto(rebuilt, cursor)
            cursor += newUdta.size
        }

        val delta = rebuilt.size - moovBody.size
        if (delta != 0 && mdat.start > moov.end && !shiftChunkOffsets(rebuilt, delta)) {
            return false
        }

        val temp = File(file.parentFile, "${file.name}.tagtmp")
        temp.outputStream().use { out ->
            out.write(bytes, 0, moov.start)
            out.write(box("moov", rebuilt))
            out.write(bytes, moov.end, bytes.size - moov.end)
        }
        if (!temp.renameTo(file)) {
            temp.copyTo(file, overwrite = true)
            temp.delete()
        }
        return file.isFile && file.length() > 0L
    }

    fun hasCover(file: File): Boolean = runCatching {
        val bytes = file.readBytes()
        val top = boxes(bytes, 0, bytes.size) ?: return@runCatching false
        val moov = top.firstOrNull { it.type == "moov" } ?: return@runCatching false
        String(bytes, moov.start, moov.size, Charsets.ISO_8859_1).contains("covr")
    }.getOrDefault(false)

    // -------------------------------------------------------------- atoms --

    private class Box(val type: String, val start: Int, val end: Int) {
        val size: Int get() = end - start
    }

    private fun boxes(bytes: ByteArray, from: Int, to: Int): List<Box>? {
        val list = ArrayList<Box>()
        var offset = from
        while (offset + 8 <= to) {
            var size = readInt(bytes, offset)
            val type = String(bytes, offset + 4, 4, Charsets.ISO_8859_1)
            var header = 8
            if (size == 1) {
                if (offset + 16 > to) return null
                size = readLong(bytes, offset + 8).toInt()
                header = 16
            } else if (size == 0) {
                size = to - offset
            }
            if (size < header || offset + size > to) return null
            list += Box(type, offset, offset + size)
            offset += size
        }
        return list
    }

    private fun rebuildUdta(moovBody: ByteArray, udta: Box, ilst: ByteArray): ByteArray? {
        val udtaBody = moovBody.copyOfRange(udta.start + 8, udta.end)
        val udtaChildren = boxes(udtaBody, 0, udtaBody.size) ?: return null
        val existingMeta = udtaChildren.firstOrNull { it.type == "meta" }

        val newMeta = if (existingMeta != null) {
            val metaBody = udtaBody.copyOfRange(existingMeta.start + 8, existingMeta.end)
            if (metaBody.size < 4) return null
            val metaChildren = boxes(metaBody, 4, metaBody.size) ?: return null
            val existingIlst = metaChildren.firstOrNull { it.type == "ilst" }
            val hasHandler = metaChildren.any { it.type == "hdlr" }
            val bodySize = metaChildren.sumOf { it.size }
            val payload = ByteArray(
                4 + bodySize - (existingIlst?.size ?: 0) +
                    ilst.size + (if (hasHandler) 0 else handler().size),
            )
            var cursor = 0
            metaBody.copyInto(payload, cursor, 0, 4)
            cursor += 4
            if (!hasHandler) {
                handler().copyInto(payload, cursor)
                cursor += handler().size
            }
            for (child in metaChildren) {
                if (child === existingIlst) {
                    ilst.copyInto(payload, cursor)
                    cursor += ilst.size
                } else {
                    metaBody.copyInto(payload, cursor, child.start, child.end)
                    cursor += child.size
                }
            }
            if (existingIlst == null) {
                ilst.copyInto(payload, cursor)
                cursor += ilst.size
            }
            box("meta", payload)
        } else {
            box("meta", fullBox() + handler() + ilst)
        }

        val payload = ByteArray(
            udtaBody.size - (existingMeta?.size ?: 0) + newMeta.size,
        )
        var cursor = 0
        for (child in udtaChildren) {
            if (child === existingMeta) {
                newMeta.copyInto(payload, cursor)
                cursor += newMeta.size
            } else {
                udtaBody.copyInto(payload, cursor, child.start, child.end)
                cursor += child.size
            }
        }
        if (existingMeta == null) {
            newMeta.copyInto(payload, cursor)
            cursor += newMeta.size
        }
        return box("udta", payload)
    }

    private fun buildMetadata(meta: AudioMeta, cover: ByteArray?): ByteArray {
        val out = java.io.ByteArrayOutputStream()
        textAtom("\u00A9nam", meta.title).takeIf { it.isNotEmpty() }?.let(out::write)
        textAtom("\u00A9ART", meta.artist).takeIf { it.isNotEmpty() }?.let(out::write)
        textAtom("aART", meta.artist).takeIf { it.isNotEmpty() }?.let(out::write)
        textAtom("\u00A9cmt", "Source: ${meta.sourceUrl}").takeIf { it.isNotEmpty() }
            ?.let(out::write)
        textAtom("\u00A9too", "ReelRelay").takeIf { it.isNotEmpty() }?.let(out::write)
        if (cover != null && cover.isNotEmpty()) out.write(box("covr", dataAtom(13, cover)))
        return out.toByteArray()
    }

    private fun textAtom(name: String, value: String): ByteArray {
        val cleaned = cleanTag(value)
        if (cleaned.isEmpty()) return ByteArray(0)
        return box(name, dataAtom(1, cleaned.toByteArray(Charsets.UTF_8)))
    }

    private fun dataAtom(type: Int, payload: ByteArray): ByteArray {
        val body = ByteArray(8 + payload.size)
        writeInt(body, 0, type)
        payload.copyInto(body, 8)
        return box("data", body)
    }

    /** Apple's `meta` handler: 4 reserved bytes, then `mdir`, then a name. */
    private fun handler(): ByteArray {
        val body = ByteArray(4 + 4 + 4 + 12 + 1)
        "mdir".toByteArray(Charsets.US_ASCII).copyInto(body, 8)
        "appl".toByteArray(Charsets.US_ASCII).copyInto(body, 24)
        return box("hdlr", body)
    }

    private fun fullBox(): ByteArray = ByteArray(4)

    private fun box(type: String, payload: ByteArray): ByteArray {
        val out = ByteArray(8 + payload.size)
        writeInt(out, 0, out.size)
        type.toByteArray(Charsets.ISO_8859_1).copyInto(out, 4)
        payload.copyInto(out, 8)
        return out
    }

    // ------------------------------------------------------------ offsets --

    /** Adds [delta] to every chunk offset so the shifted `mdat` stays valid. */
    private fun shiftChunkOffsets(moov: ByteArray, delta: Int): Boolean {
        var changed = false
        fun patch(offset: Int, type: String): Boolean {
            return when (type) {
                "stco" -> {
                    val entries = readInt(moov, offset + 12)
                    var index = offset + 16
                    repeat(entries) {
                        writeInt(moov, index, readInt(moov, index) + delta)
                        index += 4
                    }
                    changed = true
                    true
                }
                "co64" -> {
                    val entries = readInt(moov, offset + 12)
                    var index = offset + 16
                    repeat(entries) {
                        writeLong(moov, index, readLong(moov, index) + delta)
                        index += 8
                    }
                    changed = true
                    true
                }
                else -> false
            }
        }

        fun walk(from: Int, to: Int): Boolean {
            var offset = from
            while (offset + 8 <= to) {
                val size = readInt(moov, offset)
                val type = String(moov, offset + 4, 4, Charsets.ISO_8859_1)
                if (size < 8 || offset + size > to) return false
                if (patch(offset, type)) {
                    // Chunk offsets patched in place.
                } else if (type in CONTAINERS) {
                    if (!walk(offset + 8, offset + size)) return false
                } else if (type == "meta") {
                    if (!walk(offset + 12, offset + size)) return false
                }
                offset += size
            }
            return true
        }
        return walk(0, moov.size) && changed
    }

    // ------------------------------------------------------------- binary --

    private fun readInt(bytes: ByteArray, offset: Int): Int =
        ByteBuffer.wrap(bytes, offset, 4).order(ByteOrder.BIG_ENDIAN).int

    private fun writeInt(bytes: ByteArray, offset: Int, value: Int) {
        ByteBuffer.wrap(bytes, offset, 4).order(ByteOrder.BIG_ENDIAN).putInt(value)
    }

    private fun readLong(bytes: ByteArray, offset: Int): Long =
        ByteBuffer.wrap(bytes, offset, 8).order(ByteOrder.BIG_ENDIAN).long

    private fun writeLong(bytes: ByteArray, offset: Int, value: Long) {
        ByteBuffer.wrap(bytes, offset, 8).order(ByteOrder.BIG_ENDIAN).putLong(value)
    }
}
