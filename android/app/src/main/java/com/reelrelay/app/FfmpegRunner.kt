package com.reelrelay.app

import android.content.Context
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

    suspend fun merge(context: Context, video: File, audio: File, output: File) {
        run(
            context,
            listOf(
                "-y",
                "-i", video.absolutePath,
                "-i", audio.absolutePath,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c", "copy",
                "-movflags", "+faststart",
                output.absolutePath,
            ),
        )
    }

    suspend fun convertAudio(
        context: Context,
        source: File,
        output: File,
        format: String,
        codec: String?,
    ) {
        val args = mutableListOf("-y", "-i", source.absolutePath, "-vn")
        when (format.lowercase()) {
            "mp3" -> args += listOf("-c:a", "libmp3lame", "-b:a", "320k")
            "m4a" -> if (codec?.startsWith("mp4a") == true) {
                args += listOf("-c:a", "copy")
            } else {
                args += listOf("-c:a", "aac", "-b:a", "256k")
            }
            else -> throw ParallelUnavailable("Unsupported audio format: $format")
        }
        args += output.absolutePath
        run(context, args)
    }

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
            process.destroyForcibly()
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
