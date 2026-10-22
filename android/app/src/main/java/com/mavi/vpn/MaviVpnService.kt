package com.mavi.vpn

import android.app.Notification
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.os.PowerManager
import android.util.Log
import android.widget.Toast
import com.mavi.vpn.data.PrefsManager
import com.mavi.vpn.nativelib.NativeLib
import com.mavi.vpn.service.NotificationHelper

class MaviVpnService : VpnService() {
    private var vpnInterface: ParcelFileDescriptor? = null

    @Volatile private var thread: Thread? = null
    private var initId = 0L

    private var connectivityManager: ConnectivityManager? = null

    @Volatile private var networkCallback: ConnectivityManager.NetworkCallback? = null

    @Volatile private var isRunning = false
    private var isForeground = false
    private var wakeLock: PowerManager.WakeLock? = null
    private val vpnLock = Any()

    /** Single source of truth for the active native session handle + generation.
     *  Shares [vpnLock] so compound session operations stay atomic. */
    private val handleRegistry = SessionHandleRegistry(vpnLock)

    private lateinit var prefs: PrefsManager
    private lateinit var notificationHelper: NotificationHelper
    private lateinit var tokenManager: KeycloakTokenManager
    private lateinit var pauseScheduler: VpnPauseScheduler

    companion object {
        private val connectionStatus = VpnConnectionStatus()
        val connectionState = connectionStatus.state
        val pausedUntil = connectionStatus.pausedUntil
    }

    override fun onCreate() {
        super.onCreate()
        prefs = PrefsManager(this)
        notificationHelper = NotificationHelper(this)
        tokenManager = KeycloakTokenManager(PrefsKeycloakTokenStore(prefs))
        pauseScheduler = VpnPauseScheduler(this, ::resumeVpn)
    }

    override fun onStartCommand(
        intent: Intent?,
        flags: Int,
        startId: Int,
    ): Int {
        val action = intent?.action

        when (action) {
            "STOP" -> {
                stopVpn()
                return START_NOT_STICKY
            }
            ACTION_PAUSE -> {
                pauseVpn(intent.getIntExtra(EXTRA_PAUSE_MINUTES, 0))
                return stickyWhileActive()
            }
            ACTION_RESUME -> {
                resumeVpn()
                return stickyWhileActive()
            }
        }

        val request = resolveVpnStartRequest(intent, prefs)
        if (request != null && startVpnIfConfigured(request)) {
            return START_STICKY
        }
        return START_NOT_STICKY
    }

    // Keeps a paused session sticky too: if the process dies mid-pause, the
    // system restart reconnects instead of leaving the tunnel off for good.
    private fun stickyWhileActive(): Int = if (connectionStatus.state.value.canStop) START_STICKY else START_NOT_STICKY

    private fun startVpnIfConfigured(request: VpnStartRequest): Boolean {
        if (request.ip.isEmpty() || !vpnStartHasCredentials(prefs, request.token)) {
            Log.e("MaviVPN", "Cannot restart: Credentials missing.")
            return false
        }
        startVpn(request)
        return true
    }

    /** Tears the tunnel down but keeps the service in the foreground until the
     *  pause runs out, so traffic is never left unprotected indefinitely. */
    private fun pauseVpn(minutes: Int) {
        val durationMillis = pauseDurationMillis(minutes)
        if (durationMillis == null) {
            Log.w("MaviVPN", "Ignoring pause request with unsupported duration: $minutes min")
            return
        }
        if (!connectionStatus.state.value.canStop) return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && isLockdownEnabled) {
            // With "Block connections without VPN" a pause would only cut the device off.
            Toast.makeText(this, "Pause is unavailable while Android blocks connections without VPN.", Toast.LENGTH_LONG).show()
            return
        }

        val cleanup = invalidateCurrentSession()
        stopCurrentSession(cleanup)
        releaseWakeLock()

        val resumeAt = System.currentTimeMillis() + durationMillis
        connectionStatus.pause(resumeAt)
        pauseScheduler.schedule(durationMillis)
        showForegroundNotification(notificationHelper.createPausedNotification(resumeAt))
        Log.i("MaviVPN", "VPN paused for $minutes min")
    }

    private fun resumeVpn() {
        if (connectionStatus.state.value != VpnConnectionState.PAUSED) return
        Log.i("MaviVPN", "Resuming paused VPN")
        if (!startVpnIfConfigured(savedVpnStartRequest(prefs))) {
            stopVpn()
        }
    }

    private fun startVpn(request: VpnStartRequest) {
        pauseScheduler.cancel()
        val cleanup = invalidateCurrentSession()
        stopCurrentSession(cleanup)
        val sessionGeneration = cleanup.generation
        initId = NativeLib.prepareInit()

        registerNetworkCallback(sessionGeneration)

        val notification =
            notificationHelper.createNotification("Mavi VPN", "Connecting to ${request.ip}...")
        acquireWakeLock()

        isRunning = true
        connectionStatus.start()
        showForegroundNotification(notification)

        val callbacks =
            VpnSessionCallbacks(
                isRunning = { isRunning && handleRegistry.isCurrent(sessionGeneration) },
                setRunning = { value ->
                    handleRegistry.withCurrent(sessionGeneration) {
                        isRunning = value
                        if (!value) connectionStatus.stop()
                    }
                },
                setConnected = { value ->
                    handleRegistry.withCurrent(sessionGeneration) { connectionStatus.connected(value) }
                },
                attachInterface = { localInterface ->
                    handleRegistry.withCurrent(sessionGeneration) { attachVpnInterface(localInterface) }
                },
                detachInterface = ::detachVpnInterface,
                releaseNativeHandle = ::releaseNativeHandle,
            )
        val sessionThread =
            VpnSessionWorker(
                vpnService = this,
                request = request,
                prefs = prefs,
                notificationHelper = notificationHelper,
                tokenManager = tokenManager,
                handleRegistry = handleRegistry,
                sessionGeneration = sessionGeneration,
                initId = initId,
                callbacks = callbacks,
            ).createThread()
        thread = sessionThread
        sessionThread.start()
    }

    private fun registerNetworkCallback(sessionGeneration: Long) {
        try {
            connectivityManager = getSystemService(ConnectivityManager::class.java)
            networkCallback =
                object : ConnectivityManager.NetworkCallback() {
                    override fun onAvailable(network: Network) {
                        Log.d("MaviVPN", "Network available: $network")
                        handleRegistry.withHandleIfCurrent(sessionGeneration, NativeLib::networkChanged)
                    }

                    override fun onLost(network: Network) {
                        Log.d("MaviVPN", "Network lost: $network")
                    }
                }
            connectivityManager?.registerDefaultNetworkCallback(networkCallback!!)
        } catch (e: Exception) {
            Log.e("MaviVPN", "Failed to register network callback", e)
        }
    }

    private fun attachVpnInterface(localInterface: ParcelFileDescriptor?) {
        synchronized(vpnLock) {
            vpnInterface = localInterface
        }
    }

    private fun detachVpnInterface(localInterface: ParcelFileDescriptor?) {
        synchronized(vpnLock) {
            if (vpnInterface == localInterface) {
                vpnInterface = null
            }
        }
    }

    private fun stopVpn() {
        tearDownVpnSession()
        stopSelf()
    }

    private fun tearDownVpnSession() {
        pauseScheduler.cancel()
        val cleanup = invalidateCurrentSession()
        stopCurrentSession(cleanup)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
        isForeground = false

        releaseWakeLock()
    }

    /** A service that is already in the foreground (e.g. resuming from a pause
     *  via alarm) only swaps its notification instead of re-entering foreground. */
    private fun showForegroundNotification(notification: Notification) {
        if (isForeground) {
            notificationHelper.notify(1, notification)
        } else {
            startForeground(1, notification)
            isForeground = true
        }
    }

    private fun invalidateCurrentSession(): SessionCleanup {
        synchronized(vpnLock) {
            val invalidation = handleRegistry.invalidate()
            NativeLib.cancelInit(initId)
            initId = 0L
            if (invalidation.previousHandle != 0L) {
                // The worker frees this handle after its native loop exits. Do
                // the stop while retaining the same monitor used by callbacks
                // and free, so this pointer cannot be freed concurrently.
                NativeLib.stop(invalidation.previousHandle)
            }
            isRunning = false
            connectionStatus.stop()
            val cleanup =
                SessionCleanup(
                    handle = invalidation.previousHandle,
                    workerThread = thread,
                    callback = networkCallback,
                    vpnInterface = vpnInterface,
                    generation = invalidation.generation,
                )
            thread = null
            networkCallback = null
            vpnInterface = null
            return cleanup
        }
    }

    private fun stopCurrentSession(cleanup: SessionCleanup) {
        try {
            if (connectivityManager != null && cleanup.callback != null) {
                connectivityManager?.unregisterNetworkCallback(cleanup.callback)
            }
        } catch (e: Exception) {
            Log.w("MaviVPN", "Failed to unregister network callback: ${e.message}")
        }

        try {
            cleanup.vpnInterface?.close()
        } catch (e: Exception) {
            e.printStackTrace()
        }

        try {
            if (cleanup.workerThread != null && cleanup.workerThread != Thread.currentThread()) {
                cleanup.workerThread.join(3000)
            }
        } catch (_: Exception) {
        }
    }

    /**
     * Detaches and frees a worker-owned handle while holding the shared native
     * handle monitor. Native callbacks use the same monitor through
     * [SessionHandleRegistry.withHandleIfCurrent].
     */
    private fun releaseNativeHandle(
        handle: Long,
        stopFirst: Boolean = false,
    ) {
        synchronized(vpnLock) {
            handleRegistry.clearIfMatches(handle)
            if (stopFirst) {
                NativeLib.stop(handle)
            }
            NativeLib.free(handle)
        }
    }

    /**
     * Called by the system when the user revokes this VPN's permission from
     * Settings. The default [VpnService] implementation just calls
     * [stopSelf], which would skip our own teardown (native session handle,
     * TUN fd, network callback, wake lock) entirely - so this must run the
     * same path as a manual Stop.
     */
    override fun onRevoke() {
        Log.i("MaviVPN", "VPN permission revoked by the system; tearing down session")
        stopVpn()
    }

    override fun onDestroy() {
        tearDownVpnSession()
        super.onDestroy()
    }

    private fun acquireWakeLock() {
        releaseWakeLock()
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock =
            pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "MaviVPN::ServiceWakeLock").apply {
                setReferenceCounted(false)
                acquire()
            }
    }

    private fun releaseWakeLock() {
        try {
            if (wakeLock?.isHeld == true) {
                wakeLock?.release()
            }
        } catch (e: Exception) {
            Log.w("MaviVPN", "Error releasing WakeLock: ${e.message}")
        }
        wakeLock = null
    }
}
