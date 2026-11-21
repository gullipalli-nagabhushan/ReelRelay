package com.reelrelay.app

import android.app.Application
import androidx.core.content.edit
import com.yausername.ffmpeg.FFmpeg
import com.yausername.youtubedl_android.YoutubeDL
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import java.io.File

/**
 * Boots the bundled Python/yt-dlp/ffmpeg toolchain once per process.
 *
 * Unpacking the native payload takes a few seconds, so it runs on a background
 * thread while the UI is already interactive. Every extraction/download awaits
 * [engineReady] first.
 */
class ReelRelayApp : Application() {

    private val engine = CompletableDeferred<Result<Unit>>()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    val engineReady: CompletableDeferred<Result<Unit>> get() = engine

    override fun onCreate() {
        super.onCreate()
        scope.launch {
            if (!engine.isCompleted) {
                engine.complete(
                    runCatching {
                        refreshBundledYtDlp()
                        YoutubeDL.getInstance().init(this@ReelRelayApp)
                        FFmpeg.getInstance().init(this@ReelRelayApp)
                    }
                )
            }
        }
    }

    /**
     * youtubedl-android unpacks its `res/raw/ytdlp` only when no copy exists,
     * so a newer build shipped in an app update would otherwise be ignored.
     * The extracted copy is dropped once whenever [BUNDLED_YTDLP_VERSION]
     * changes, then recreated from the bundled resource on init.
     */
    private fun refreshBundledYtDlp() {
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        if (prefs.getString(KEY_YTDLP_VERSION, null) == BUNDLED_YTDLP_VERSION) return
        // Only the yt-dlp zipapp lives here; Python and ffmpeg are untouched.
        File(noBackupFilesDir, YTDLP_PACKAGE_DIR).deleteRecursively()
        prefs.edit { putString(KEY_YTDLP_VERSION, BUNDLED_YTDLP_VERSION) }
    }

    suspend fun awaitEngine(): Result<Unit> = engine.await()

    private companion object {
        const val PREFS = "reelrelay-engine"
        const val KEY_YTDLP_VERSION = "ytdlp-version"
        const val YTDLP_PACKAGE_DIR = "youtubedl-android/packages/yt-dlp"

        /** Keep in sync with `android/app/src/main/res/raw/ytdlp`. */
        const val BUNDLED_YTDLP_VERSION = "2026.10.26"
    }
}
