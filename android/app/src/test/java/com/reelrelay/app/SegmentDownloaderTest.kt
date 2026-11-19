package com.reelrelay.app

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okio.Buffer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.util.concurrent.atomic.AtomicLong
import kotlin.random.Random

class SegmentDownloaderTest {

    @Test
    fun `downloads a stream over parallel ranges`() {
        val body = Random(7).nextBytes(3 * 1024 * 1024 + 17)
        serve(body) { url ->
            val dir = Files.createTempDirectory("rr-segments").toFile()
            try {
                val target = File(dir, "video.bin")
                val lastDone = AtomicLong(0)
                val lastTotal = AtomicLong(0)
                val total = SegmentDownloader(connections = 4, segmentBytes = 256 * 1024)
                    .download(url, emptyMap(), target) { done, all ->
                        lastDone.set(done)
                        lastTotal.set(all)
                    }

                assertEquals(body.size.toLong(), total)
                assertEquals(total, lastDone.get())
                assertEquals(total, lastTotal.get())
                assertArrayEquals(body, target.readBytes())
            } finally {
                dir.deleteRecursively()
            }
        }
    }

    @Test
    fun `falls back when the origin ignores range requests`() {
        val body = ByteArray(3 * 1024 * 1024) { (it % 251).toByte() }
        serve(body, supportsRange = false) { url ->
            val dir = Files.createTempDirectory("rr-segments").toFile()
            try {
                assertThrows(ParallelUnavailable::class.java) {
                    runBlocking {
                        SegmentDownloader(connections = 2, segmentBytes = 256 * 1024)
                            .download(url, emptyMap(), File(dir, "video.bin"))
                    }
                }
            } finally {
                dir.deleteRecursively()
            }
        }
    }

    @Test
    fun `small streams are left to yt-dlp`() {
        val body = Random(3).nextBytes(1024)
        serve(body) { url ->
            val dir = Files.createTempDirectory("rr-segments").toFile()
            try {
                assertThrows(ParallelUnavailable::class.java) {
                    runBlocking {
                        SegmentDownloader().download(url, emptyMap(), File(dir, "clip.bin"))
                    }
                }
            } finally {
                dir.deleteRecursively()
            }
        }
    }

    @Test
    fun `file names are safe for MediaStore`() {
        assertEquals("A_B_C", sanitizeFileName("A/B\\C"))
        assertEquals("ReelRelay", sanitizeFileName("   "))
        assertTrue(sanitizeFileName("x".repeat(400)).length <= 150)
    }

    private fun serve(
        body: ByteArray,
        supportsRange: Boolean = true,
        block: suspend (String) -> Unit,
    ) {
        val server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val match = RANGE.find(request.getHeader("Range") ?: "")
                if (supportsRange && match != null) {
                    val start = match.groupValues[1].toInt()
                    val end = match.groupValues[2].toIntOrNull()
                        ?.coerceAtMost(body.size - 1)
                        ?: (body.size - 1)
                    val chunk = body.copyOfRange(start, end + 1)
                    return MockResponse()
                        .setResponseCode(206)
                        .addHeader(
                            "Content-Range",
                            "bytes $start-${start + chunk.size - 1}/${body.size}",
                        )
                        .setBody(Buffer().write(chunk))
                }
                return MockResponse().setResponseCode(200).setBody(Buffer().write(body))
            }
        }
        server.start()
        try {
            runBlocking { block(server.url("/media").toString()) }
        } finally {
            server.shutdown()
        }
    }

    private companion object {
        val RANGE = Regex("bytes=(\\d+)-(\\d*)")
    }
}
