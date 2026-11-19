package com.reelrelay.app

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.Call
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.util.Collections
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.min

/** The origin cannot be split into parallel ranges, so yt-dlp should handle it. */
class ParallelUnavailable(message: String) : Exception(message)

/**
 * Downloads one direct HTTP stream with several ranged connections.
 *
 * Mobile CDNs throttle a single connection, so a 4K track can crawl even on a
 * fast line. The stream is split into fixed-size segments that workers fetch
 * with `Range` requests and write at the right offset. Segments are written
 * straight to the destination file, so memory stays at a few buffers per
 * worker no matter how large the video is.
 */
class SegmentDownloader(
    private val client: OkHttpClient = defaultClient(),
    private val connections: Int = DEFAULT_CONNECTIONS,
    private val segmentBytes: Long = DEFAULT_SEGMENT_BYTES,
) {

    suspend fun download(
        url: String,
        headers: Map<String, String>,
        destination: File,
        expectedSize: Long = 0L,
        progress: ((Long, Long) -> Unit)? = null,
    ): Long {
        val total = probe(url, headers, expectedSize)
        if (total < MIN_PARALLEL_BYTES) {
            throw ParallelUnavailable("Stream is too small to split")
        }

        val segmentCount = ((total + segmentBytes - 1) / segmentBytes).toInt()
        val workers = min(connections, segmentCount).coerceAtLeast(1)
        val next = AtomicLong(0)
        val downloaded = AtomicLong(0)
        val lastReport = AtomicLong(System.currentTimeMillis())
        val calls: MutableSet<Call> = Collections.synchronizedSet(mutableSetOf<Call>())

        // Cancel in-flight sockets as soon as the caller cancels the job.
        currentCoroutineContext()[Job]?.invokeOnCompletion {
            synchronized(calls) { calls.forEach { it.cancel() } }
        }

        destination.parentFile?.mkdirs()
        destination.delete()

        try {
            RandomAccessFile(destination, "rw").use { file ->
                file.setLength(total)
                val channel = file.channel
                coroutineScope {
                    repeat(workers) {
                        launch(Dispatchers.IO) {
                            while (isActive) {
                                val index = next.getAndIncrement()
                                if (index >= segmentCount) break
                                fetchSegment(
                                    index = index.toInt(),
                                    segmentCount = segmentCount,
                                    total = total,
                                    url = url,
                                    headers = headers,
                                    channel = channel,
                                    calls = calls,
                                    downloaded = downloaded,
                                    lastReport = lastReport,
                                    progress = progress,
                                )
                            }
                        }
                    }
                }
            }
        } catch (t: Throwable) {
            destination.delete()
            throw t
        } finally {
            synchronized(calls) { calls.forEach { it.cancel() } }
        }

        if (downloaded.get() != total) {
            destination.delete()
            throw IOException("Only ${downloaded.get()} of $total bytes arrived")
        }
        return total
    }

    private fun probe(url: String, headers: Map<String, String>, expectedSize: Long): Long {
        val request = buildRequest(url, headers).newBuilder()
            .header("Range", "bytes=0-1")
            .build()
        client.newCall(request).execute().use { response ->
            if (response.code != 206) {
                throw ParallelUnavailable("Origin ignored range requests (HTTP ${response.code})")
            }
            val contentRange = response.header("Content-Range")
            val parsed = contentRange
                ?.let { CONTENT_RANGE.find(it)?.groupValues?.getOrNull(1)?.toLongOrNull() }
            val total = parsed ?: expectedSize
            if (total <= 0L) {
                throw ParallelUnavailable("Origin did not report a stream size")
            }
            return total
        }
    }

    private suspend fun fetchSegment(
        index: Int,
        segmentCount: Int,
        total: Long,
        url: String,
        headers: Map<String, String>,
        channel: FileChannel,
        calls: MutableSet<Call>,
        downloaded: AtomicLong,
        lastReport: AtomicLong,
        progress: ((Long, Long) -> Unit)?,
    ) {
        val start = index * segmentBytes
        val end = min(total, start + segmentBytes) - 1
        val expected = end - start + 1
        var failure: Throwable? = null

        for (attempt in 0 until RETRIES) {
            currentCoroutineContext().ensureActive()
            val call = client.newCall(
                buildRequest(url, headers).newBuilder()
                    .header("Range", "bytes=$start-$end")
                    .build()
            )
            synchronized(calls) { calls.add(call) }
            var written = 0L
            try {
                call.execute().use { response ->
                    if (response.code == 416) {
                        throw ParallelUnavailable("Origin rejected the range request")
                    }
                    if (response.code != 206) {
                        throw IOException("Origin returned HTTP ${response.code}")
                    }
                    val body = response.body ?: throw IOException("Empty response body")
                    var position = start
                    body.byteStream().use { stream ->
                        val buffer = ByteArray(BUFFER_BYTES)
                        while (written < expected) {
                            val room = min(buffer.size.toLong(), expected - written).toInt()
                            val read = stream.read(buffer, 0, room)
                            if (read < 0) break
                            val slice = ByteBuffer.wrap(buffer, 0, read)
                            while (slice.hasRemaining()) {
                                position += channel.write(slice, position)
                            }
                            written += read
                            report(progress, lastReport, downloaded.addAndGet(read.toLong()), total)
                        }
                    }
                    if (written < expected) {
                        throw IOException("Segment ${index + 1} was short: $written/$expected")
                    }
                }
                return
            } catch (cancel: CancellationException) {
                throw cancel
            } catch (unsupported: ParallelUnavailable) {
                throw unsupported
            } catch (t: Throwable) {
                // The retry overwrites these bytes, so do not count them twice.
                downloaded.addAndGet(-written)
                failure = t
                delay(RETRY_DELAY_MS * (attempt + 1))
            } finally {
                synchronized(calls) { calls.remove(call) }
            }
        }
        throw IOException("Segment ${index + 1}/$segmentCount failed", failure)
    }

    private fun buildRequest(url: String, headers: Map<String, String>): Request {
        val builder = Request.Builder().url(url)
        for ((key, value) in headers) {
            if (key.lowercase() in SKIPPED_HEADERS) continue
            builder.header(key, value)
        }
        builder.header("Accept-Encoding", "identity")
        return builder.build()
    }

    companion object {
        const val DEFAULT_CONNECTIONS = 12
        const val DEFAULT_SEGMENT_BYTES = 4L * 1024 * 1024

        private const val MIN_PARALLEL_BYTES = 2L * 1024 * 1024
        private const val RETRIES = 3
        private const val BUFFER_BYTES = 64 * 1024
        private const val RETRY_DELAY_MS = 400L
        private val CONTENT_RANGE = Regex("bytes\\s+\\d+-\\d+/(\\d+)")
        private val SKIPPED_HEADERS = setOf(
            "range",
            "accept-encoding",
            "content-length",
            "host",
            "connection",
            "transfer-encoding",
        )

        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .followRedirects(true)
            .build()

        private fun report(
            progress: ((Long, Long) -> Unit)?,
            lastReport: AtomicLong,
            done: Long,
            total: Long,
        ) {
            if (progress == null) return
            val now = System.currentTimeMillis()
            val previous = lastReport.get()
            if (done >= total || now - previous >= 200L) {
                if (lastReport.compareAndSet(previous, now)) {
                    progress(done, total)
                }
            }
        }
    }
}
