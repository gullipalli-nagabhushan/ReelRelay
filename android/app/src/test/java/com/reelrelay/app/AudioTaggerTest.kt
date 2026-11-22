package com.reelrelay.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class AudioTaggerTest {

    private val meta = AudioMeta(
        title = "Tëst ✓",
        artist = "Some Channel",
        sourceUrl = "https://www.youtube.com/watch?v=abc",
    )

    private fun fixture(name: String): File {
        val source = File(javaClass.classLoader!!.getResource(name)!!.toURI())
        val target = File.createTempFile("reelrelay-$name-", ".$name")
        target.deleteOnExit()
        source.copyTo(target, overwrite = true)
        return target
    }

    private fun cover(name: String = "cover.jpg"): File = fixture(name)

    private fun audioTail(file: File): ByteArray {
        val bytes = file.readBytes()
        if (file.extension == "mp3") {
            val start = if (bytes.size >= 10 && String(bytes, 0, 3) == "ID3") {
                10 + (((bytes[6].toInt() and 0x7F) shl 21) or
                    ((bytes[7].toInt() and 0x7F) shl 14) or
                    ((bytes[8].toInt() and 0x7F) shl 7) or
                    (bytes[9].toInt() and 0x7F))
            } else {
                0
            }
            val end = if (bytes.size >= 128 && String(bytes, bytes.size - 128, 3) == "TAG") {
                bytes.size - 128
            } else {
                bytes.size
            }
            return bytes.copyOfRange(start, end)
        }
        // FLAC: skip the metadata blocks.
        var offset = 4
        while (offset + 4 <= bytes.size) {
            val last = bytes[offset].toInt() and 0x80 != 0
            val length = ((bytes[offset + 1].toInt() and 0xFF) shl 16) or
                ((bytes[offset + 2].toInt() and 0xFF) shl 8) or
                (bytes[offset + 3].toInt() and 0xFF)
            offset += 4 + length
            if (last) break
        }
        return bytes.copyOfRange(offset, bytes.size)
    }

    @Test
    fun `mp3 gets tags and a front cover without touching audio frames`() {
        val file = fixture("sample.mp3")
        val originalAudio = audioTail(file)

        assertTrue(AudioTagger.embed(file, meta, cover()))
        assertTrue(AudioTagger.hasCover(file))

        val bytes = file.readBytes()
        assertEquals("ID3", String(bytes, 0, 3))
        val tag = String(bytes, 0, minOf(bytes.size, 8192), Charsets.ISO_8859_1)
        assertTrue(tag.contains("TIT2"))
        assertTrue(tag.contains("TPE1"))
        assertTrue(tag.contains("TPE2"))
        assertTrue(tag.contains("COMM"))
        assertTrue(tag.contains("APIC"))
        assertTrue(tag.contains("TSSE"))
        assertTrue(originalAudio.contentEquals(audioTail(file)))
    }

    @Test
    fun `tagging the same mp3 twice is stable`() {
        val file = fixture("sample.mp3")
        val picture = cover()

        assertTrue(AudioTagger.embed(file, meta, picture))
        val first = file.length()
        assertTrue(AudioTagger.embed(file, meta, picture))
        assertEquals(first, file.length())
    }

    @Test
    fun `mp3 still gets text tags when no cover is available`() {
        val file = fixture("sample.mp3")
        val originalAudio = audioTail(file)

        assertTrue(AudioTagger.embed(file, meta, null))
        assertFalse(AudioTagger.hasCover(file))

        val bytes = file.readBytes()
        val tag = String(bytes, 0, minOf(bytes.size, 4096), Charsets.ISO_8859_1)
        assertTrue(tag.contains("TIT2"))
        assertTrue(tag.contains("TSSE"))
        assertFalse(tag.contains("APIC"))
        assertTrue(originalAudio.contentEquals(audioTail(file)))
    }

    @Test
    fun `flac gets vorbis comments and a picture block`() {
        val file = fixture("sample.flac")
        val originalAudio = audioTail(file)

        assertTrue(AudioTagger.embed(file, meta, cover()))
        assertTrue(AudioTagger.hasCover(file))

        val bytes = file.readBytes()
        assertEquals("fLaC", String(bytes, 0, 4))
        val tag = String(bytes, 0, minOf(bytes.size, 4096), Charsets.UTF_8)
        assertTrue(tag.contains("TITLE=Tëst ✓"))
        assertTrue(tag.contains("ARTIST=Some Channel"))
        assertTrue(tag.contains("ENCODER=ReelRelay"))
        assertTrue(originalAudio.contentEquals(audioTail(file)))
    }

    /** Raw `mdat` payload: it must stay byte-identical after retagging. */
    private fun mdatPayload(file: File): ByteArray {
        val bytes = file.readBytes()
        var offset = 0
        while (offset + 8 <= bytes.size) {
            val size = ((bytes[offset].toInt() and 0xFF) shl 24) or
                ((bytes[offset + 1].toInt() and 0xFF) shl 16) or
                ((bytes[offset + 2].toInt() and 0xFF) shl 8) or
                (bytes[offset + 3].toInt() and 0xFF)
            val type = String(bytes, offset + 4, 4, Charsets.ISO_8859_1)
            if (type == "mdat") return bytes.copyOfRange(offset + 8, offset + size)
            offset += size
        }
        return ByteArray(0)
    }

    @Test
    fun `m4a gets covr artwork with the movie atom first`() {
        val file = fixture("sample.m4a")
        val original = mdatPayload(file)

        assertTrue(AudioTagger.embed(file, meta, cover()))
        assertTrue(AudioTagger.hasCover(file))

        val bytes = file.readBytes()
        val atoms = String(bytes, 0, minOf(bytes.size, 8192), Charsets.ISO_8859_1)
        assertTrue(atoms.contains("covr"))
        assertTrue(atoms.contains("©nam"))
        assertTrue(String(bytes, Charsets.UTF_8).contains("Tëst ✓"))
        assertTrue(original.contentEquals(mdatPayload(file)))
    }

    @Test
    fun `m4a with the movie atom at the end stays playable`() {
        val file = fixture("sample_tail.m4a")
        val original = mdatPayload(file)

        assertTrue(AudioTagger.embed(file, meta, cover()))
        assertTrue(AudioTagger.hasCover(file))
        assertTrue(original.contentEquals(mdatPayload(file)))
    }

    @Test
    fun `tagging the same m4a twice is stable`() {
        val file = fixture("sample.m4a")
        val picture = cover()

        assertTrue(AudioTagger.embed(file, meta, picture))
        val first = file.length()
        assertTrue(AudioTagger.embed(file, meta, picture))
        assertEquals(first, file.length())
    }

    @Test
    fun `flac still gets comments when no cover is available`() {
        val file = fixture("sample.flac")
        val originalAudio = audioTail(file)

        assertTrue(AudioTagger.embed(file, meta, null))
        assertFalse(AudioTagger.hasCover(file))

        val bytes = file.readBytes()
        val tag = String(bytes, 0, minOf(bytes.size, 4096), Charsets.UTF_8)
        assertTrue(tag.contains("TITLE=Tëst ✓"))
        assertTrue(originalAudio.contentEquals(audioTail(file)))
    }

    @Test
    fun `m4a still gets text atoms when no cover is available`() {
        val file = fixture("sample.m4a")
        val original = mdatPayload(file)

        assertTrue(AudioTagger.embed(file, meta, null))
        assertFalse(AudioTagger.hasCover(file))

        val bytes = file.readBytes()
        val atoms = String(bytes, 0, minOf(bytes.size, 8192), Charsets.ISO_8859_1)
        assertTrue(atoms.contains("©nam"))
        assertFalse(atoms.contains("covr"))
        assertTrue(original.contentEquals(mdatPayload(file)))
    }

    @Test
    fun `jpeg dimensions are read from the frame header`() {
        assertEquals(160 to 90, AudioTagger.jpegSize(cover().readBytes()))
    }

    @Test
    fun `wav is not handled by the writer`() {
        assertFalse(AudioTagger.supports("wav"))
        assertTrue(AudioTagger.supports("MP3"))
        assertTrue(AudioTagger.supports("flac"))
        assertTrue(AudioTagger.supports("m4a"))
    }
}
