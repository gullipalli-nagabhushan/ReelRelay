package com.reelrelay.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.ArrayAdapter
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import coil.load
import com.google.android.material.snackbar.Snackbar
import com.reelrelay.app.databinding.ActivityMainBinding
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val viewModel: MainViewModel by viewModels()

    private var player: ExoPlayer? = null
    private var pendingDownload = false
    private var lastPreview: PreviewSource? = null
    private var lastLabels: List<String> = emptyList()
    private var lastError: String? = null

    private val storagePermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            val wantedDownload = pendingDownload
            pendingDownload = false
            if (granted && wantedDownload) {
                viewModel.startDownload()
            } else if (!granted) {
                Snackbar.make(binding.root, R.string.storage_permission, Snackbar.LENGTH_LONG).show()
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.previewButton.setOnClickListener { submit() }
        binding.downloadButton.setOnClickListener { onDownloadClicked() }
        binding.cancelButton.setOnClickListener { viewModel.cancelDownload() }
        binding.urlInput.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_GO || actionId == EditorInfo.IME_ACTION_DONE) {
                submit()
                true
            } else {
                false
            }
        }
        binding.formatInput.setOnItemClickListener { _, _, position, _ ->
            viewModel.select(position)
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                viewModel.state.collect(::render)
            }
        }

        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    private fun submit() {
        hideKeyboard()
        viewModel.extract(binding.urlInput.text?.toString().orEmpty())
    }

    private fun onDownloadClicked() {
        val needsLegacyPermission = Build.VERSION.SDK_INT < Build.VERSION_CODES.Q &&
            ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.WRITE_EXTERNAL_STORAGE,
            ) != PackageManager.PERMISSION_GRANTED
        if (needsLegacyPermission) {
            pendingDownload = true
            storagePermission.launch(Manifest.permission.WRITE_EXTERNAL_STORAGE)
        } else {
            viewModel.startDownload()
        }
    }

    private fun handleIntent(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND || intent.type != "text/plain") return
        val shared = intent.getStringExtra(Intent.EXTRA_TEXT).orEmpty()
        val url = Regex("https?://\\S+").find(shared)?.value ?: shared.trim()
        if (url.startsWith("http://") || url.startsWith("https://")) {
            binding.urlInput.setText(url)
            viewModel.extract(url)
        }
    }

    private fun render(state: MainUiState) {
        binding.loadingBar.isVisible = state.loadingInfo
        binding.previewButton.isEnabled = !state.loadingInfo && !state.downloading

        val engineText = when {
            state.engineError != null -> "Engine error: ${state.engineError}"
            state.engineReady -> "On-device engine ready — yt-dlp + ffmpeg"
            else -> getString(R.string.startup)
        }
        binding.engineStatus.text = engineText
        binding.engineStatus.setTextColor(
            ContextCompat.getColor(
                this,
                if (state.engineError != null) R.color.rr_rose else R.color.rr_cyan,
            )
        )

        val info = state.info
        binding.mediaCard.isVisible = info != null
        if (info != null) {
            binding.titleText.text = info.title
            binding.uploaderText.text = info.uploader
            binding.uploaderText.isVisible = info.uploader.isNotBlank()
            binding.metaText.text = info.durationLabel
            binding.metaText.isVisible = info.durationLabel.isNotBlank()
            binding.thumbnail.load(info.thumbnail)

            if (info.preview != lastPreview) {
                lastPreview = info.preview
                showPreview(info.preview)
            }
        } else if (lastPreview != null) {
            lastPreview = null
            releasePlayer()
            binding.playerView.isVisible = false
            binding.previewUnavailable.isVisible = false
        }

        updateChoices(state.choices, state.selectedIndex)
        binding.formatLayout.isVisible = state.choices.isNotEmpty()
        binding.downloadButton.isVisible = state.choices.isNotEmpty() && !state.downloading
        binding.downloadProgress.isVisible = state.downloading
        binding.downloadProgress.isIndeterminate = state.indeterminate
        binding.downloadProgress.progress = state.progress
        binding.statusText.text = state.status
        binding.statusText.isVisible = state.status.isNotBlank()
        binding.cancelButton.isVisible = state.downloading
        binding.urlLayout.isEnabled = !state.downloading

        val error = state.error
        if (error == null) {
            lastError = null
        } else if (error != lastError) {
            lastError = error
            Snackbar.make(binding.root, error, Snackbar.LENGTH_LONG).show()
        }
    }

    private fun updateChoices(choices: List<FormatChoice>, selected: Int) {
        val labels = choices.map { it.label }
        if (labels != lastLabels) {
            lastLabels = labels
            binding.formatInput.setAdapter(
                ArrayAdapter(this, android.R.layout.simple_list_item_1, labels)
            )
        }
        if (selected in labels.indices) {
            val text = labels[selected]
            if (binding.formatInput.text?.toString() != text) {
                binding.formatInput.setText(text, false)
            }
        }
    }

    private fun showPreview(source: PreviewSource?) {
        releasePlayer()
        if (source == null) {
            binding.playerView.isVisible = false
            binding.previewUnavailable.isVisible = true
            return
        }

        binding.previewUnavailable.isVisible = false
        binding.playerView.isVisible = true

        val headers = source.headers.toMutableMap()
        val userAgent = headers.entries
            .firstOrNull { it.key.equals("User-Agent", ignoreCase = true) }
            ?.value
            ?: "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
        headers.keys.removeAll { it.equals("User-Agent", ignoreCase = true) }

        val dataSourceFactory = DefaultHttpDataSource.Factory()
            .setUserAgent(userAgent)
            .setDefaultRequestProperties(headers)
            .setAllowCrossProtocolRedirects(true)

        val exoPlayer = ExoPlayer.Builder(this)
            .setMediaSourceFactory(DefaultMediaSourceFactory(dataSourceFactory))
            .build()
        binding.playerView.player = exoPlayer
        exoPlayer.addListener(object : Player.Listener {
            override fun onPlayerError(error: PlaybackException) {
                binding.playerView.isVisible = false
                binding.previewUnavailable.isVisible = true
            }
        })
        exoPlayer.setMediaItem(MediaItem.fromUri(source.url))
        exoPlayer.prepare()
        player = exoPlayer
    }

    private fun releasePlayer() {
        if (::binding.isInitialized) {
            binding.playerView.player = null
        }
        player?.release()
        player = null
    }

    private fun hideKeyboard() {
        val manager = getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager
        manager.hideSoftInputFromWindow(binding.urlInput.windowToken, 0)
        binding.urlInput.clearFocus()
    }

    override fun onStop() {
        player?.pause()
        super.onStop()
    }

    override fun onDestroy() {
        releasePlayer()
        super.onDestroy()
    }
}
