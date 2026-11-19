package com.reelrelay.app

import android.app.Application
import com.yausername.ffmpeg.FFmpeg
import com.yausername.youtubedl_android.YoutubeDL
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

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
                        YoutubeDL.getInstance().init(this@ReelRelayApp)
                        FFmpeg.getInstance().init(this@ReelRelayApp)
                    }
                )
            }
        }
    }

    suspend fun awaitEngine(): Result<Unit> = engine.await()
}
