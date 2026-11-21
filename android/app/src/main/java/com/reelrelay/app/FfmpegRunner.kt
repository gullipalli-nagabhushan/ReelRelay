package com.reelrelay.app

import android.content.Context
import android.os.Build
import com.yausername.ffmpeg.FFmpeg
import kotlinx.coroutines.runInterruptible
import java.io.File

/**
 * Runs the ffmpeg binary that ships inside the APK.
 *
 * youtubedl-android extracts `libffmpeg.so` into the app's native library
 * directory; yt-dlp already uses that exact path, so a plain [ProcessBuilder]
 * gets the same binary without any extra download. Only remuxing and audio
 * conversion happen here — the heavy streams are fetched by
 * [SegmentDownloader].
 */
object FfmpegRunner {

    /**
     * Muxes [video] and [audio] into [output]. [container] decides whether the
     * streaming hint is needed: `+faststart` is only valid for MP4-family files.
     */
    suspend fun merge(context: Context, video: File, audio: File, output: File, container: String) {
        val args = mutableListOf(
            "-y",
            "-i", video.absolutePath,
            "-i", audio.absolutePath,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c", "copy",
        )
        if (container.lowercase() in MP4_CONTAINERS) {
            args += listOf("-movflags", "+faststart")
        }
        args += output.absolutePath
        run(context, args)
    }

    /**
     * Converts the audio track of [source] to [format] and writes [tags].
     *
     * [cover] is attached as a front-cover picture for the containers that
     * support one (MP3, M4A, FLAC); WAV gets tags but no artwork. [bitrate] is
     * in kbps and ignored for lossless targets. When the source already is the
     * requested format and [copyWhenPossible] is set no re-encoding happens,
     * which keeps e.g. AAC 256 kbps untouched.
     */
    suspend fun convertAudio(
        context: Context,
        source: File,
        output: File,
        format: String,
        codec: String?,
        bitrate: Int = 0,
        copyWhenPossible: Boolean = true,
        copyAudio: Boolean = false,
        tags: Map<String, String> = emptyMap(),
        cover: File? = null,
    ) {
        val target = format.lowercase()
        val attachCover = cover != null && target in COVER_CONTAINERS

        val args = mutableListOf("-y", "-i", source.absolutePath)
        if (attachCover) {
            args += listOf("-i", cover!!.absolutePath, "-map", "0:a:0", "-map", "1:v:0")
            args += listOf("-c:v", "mjpeg", "-disposition:v", "attached_pic")
        } else {
            args += listOf("-map", "0:a:0")
        }

        if (copyAudio) {
            args += listOf("-c:a", "copy")
        } else when (target) {
            "mp3" -> {
                args += listOf("-c:a", "libmp3lame") + bitrateArg(bitrate, 320)
            }
            "m4a" -> if (copyWhenPossible && codec?.startsWith("mp4a") == true) {
                args += listOf("-c:a", "copy")
            } else {
                args += listOf("-c:a", "aac") + bitrateArg(bitrate, 256)
            }
            "flac" -> args += listOf("-c:a", "flac", "-compression_level", "5")
            "wav" -> args += listOf("-c:a", "pcm_s16le")
            else -> throw ParallelUnavailable("Unsupported audio format: $format")
        }
        if (target == "mp3") args += listOf("-id3v2_version", "3", "-write_id3v1", "0")

        // Replace any metadata inherited from the source stream with ours.
        args += listOf("-map_metadata", "-1")
        if (target in TAGGED_CONTAINERS) {
            for ((key, value) in tags) {
                val cleaned = cleanTag(value)
                if (cleaned.isNotEmpty()) args += listOf("-metadata", "$key=$cleaned")
            }
        }
        if (target == "m4a") args += listOf("-movflags", "+faststart")
        args += output.absolutePath
        run(context, args)
    }

    /** Decodes any image the bundled ffmpeg understands into a JPEG file. */
    suspend fun toJpeg(context: Context, source: File, output: File) {
        run(
            context,
            listOf(
                "-y",
                "-i", source.absolutePath,
                "-frames:v", "1",
                "-c:v", "mjpeg",
                "-q:v", "2",
                "-f", "image2",
                output.absolutePath,
            ),
        )
    }

    private fun bitrateArg(bitrate: Int, fallback: Int): List<String> =
        listOf("-b:a", "${if (bitrate > 0) bitrate else fallback}k")

    private suspend fun run(context: Context, args: List<String>) {
        val nativeDir = context.applicationInfo.nativeLibraryDir
        val binary = File(nativeDir, "libffmpeg.so")
        if (!binary.isFile) {
            throw ParallelUnavailable("The bundled ffmpeg binary is missing")
        }

        // The binary links against libav*.so, which the library unpacks next to
        // its package metadata rather than into the APK. Make sure that step
        // happened, then point the loader at those libraries.
        FFmpeg.getInstance().init(context)
        val libraries = ffmpegLibraryPath(nativeDir, context.noBackupFilesDir)

        val command = buildList {
            add(binary.absolutePath)
            addAll(listOf("-hide_banner", "-loglevel", "error", "-nostdin"))
            addAll(args)
        }
        val process = ProcessBuilder(command)
            .redirectErrorStream(true)
            .also { builder ->
                val environment = builder.environment()
                val inherited = environment["LD_LIBRARY_PATH"].orEmpty()
                environment["LD_LIBRARY_PATH"] = if (inherited.isEmpty()) {
                    libraries
                } else {
                    "$libraries:$inherited"
                }
            }
            .start()

        val output = StringBuilder()
        val reader = Thread({
            process.inputStream.bufferedReader().useLines { lines ->
                lines.forEach { line ->
                    synchronized(output) {
                        if (output.length < 4000) output.appendLine(line)
                    }
                }
            }
        }, "reelrelay-ffmpeg").apply {
            isDaemon = true
            start()
        }

        val code = try {
            runInterruptible { process.waitFor() }
        } catch (t: Throwable) {
            // `destroyForcibly` arrived in API 26; plain `destroy` is enough
            // to stop ffmpeg on the older devices we still support.
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                process.destroyForcibly()
            } else {
                @Suppress("DEPRECATION")
                process.destroy()
            }
            reader.interrupt()
            throw t
        }
        reader.join(1500)
        if (code != 0) {
            val detail = synchronized(output) { output.toString().takeLast(400) }
            throw IllegalStateException("ffmpeg failed (exit $code): $detail")
        }
    }
}

/** Where youtubedl-android unpacks the shared `libav*` libraries. */
internal const val FFMPEG_LIB_DIR = "youtubedl-android/packages/ffmpeg/usr/lib"

/** Containers that accept the MP4 streaming hint. */
internal val MP4_CONTAINERS = setOf("mp4", "m4v", "mov")

/** Audio containers ffmpeg can embed a cover picture into. */
internal val COVER_CONTAINERS = setOf("mp3", "m4a", "flac")

/** Audio containers that store text tags reliably. */
internal val TAGGED_CONTAINERS = setOf("mp3", "m4a", "flac", "wav")

/**
 * `LD_LIBRARY_PATH` for the bundled ffmpeg.
 *
 * The executable lives in the read-only native library directory, but it links
 * against `libavcodec.so.61` and friends, which the youtubedl-android package
 * unpacks into its own directory under [noBackupDir].
 */
internal fun ffmpegLibraryPath(nativeDir: String, noBackupDir: File): String {
    val packageLibs = File(noBackupDir, FFMPEG_LIB_DIR)
    check(packageLibs.isDirectory) { "The bundled ffmpeg libraries are missing: $packageLibs" }
    return listOf(nativeDir, packageLibs.absolutePath).joinToString(":")
}
