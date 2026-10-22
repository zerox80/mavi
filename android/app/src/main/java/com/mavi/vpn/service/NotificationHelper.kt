package com.mavi.vpn.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.os.Build
import android.text.format.DateFormat
import com.mavi.vpn.ACTION_RESUME
import com.mavi.vpn.MainActivity
import com.mavi.vpn.MaviVpnService
import java.util.Date

class NotificationHelper(private val context: Context) {
    private val channelId = "vpn_channel"
    private val notificationManager = context.getSystemService(NotificationManager::class.java)

    init {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val chan = NotificationChannel(channelId, "VPN Status", NotificationManager.IMPORTANCE_LOW)
            notificationManager?.createNotificationChannel(chan)
        }
    }

    private fun builder(): Notification.Builder =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(context, channelId)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(context)
        }

    fun createNotification(title: String, text: String): Notification {
        return builder()
            .setContentTitle(title)
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_lock_lock)
            .build()
    }

    fun createPausedNotification(resumeAtMillis: Long): Notification {
        val resumeTime = DateFormat.getTimeFormat(context).format(Date(resumeAtMillis))
        return builder()
            .setContentTitle("Mavi VPN paused")
            .setContentText("Traffic is not protected. Resume planned for $resumeTime; Android may delay it.")
            .setStyle(
                Notification.BigTextStyle().bigText(
                    "Traffic is not protected. Resume planned for $resumeTime; Android may delay it.",
                ),
            )
            .setSmallIcon(android.R.drawable.ic_media_pause)
            .setWhen(resumeAtMillis)
            .setShowWhen(true)
            .setUsesChronometer(true)
            .setChronometerCountDown(true)
            .setOngoing(true)
            .setContentIntent(openAppIntent())
            .addAction(serviceAction(android.R.drawable.ic_media_play, "Resume now", ACTION_RESUME))
            .addAction(serviceAction(android.R.drawable.ic_menu_close_clear_cancel, "Disconnect", "STOP"))
            .build()
    }

    fun notify(id: Int, notification: Notification) {
        notificationManager?.notify(id, notification)
    }

    fun updateNotification(id: Int, title: String, text: String) {
        notify(id, createNotification(title, text))
    }

    private fun openAppIntent(): PendingIntent =
        PendingIntent.getActivity(
            context,
            0,
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

    private fun serviceAction(icon: Int, title: String, action: String): Notification.Action {
        val intent =
            PendingIntent.getService(
                context,
                0,
                Intent(context, MaviVpnService::class.java).setAction(action),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
        return Notification.Action.Builder(Icon.createWithResource(context, icon), title, intent).build()
    }
}
