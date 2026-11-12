@file:Suppress("ktlint:standard:function-naming", "ktlint:standard:property-naming")

package com.mavi.vpn.ui.screens

import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.os.Build
import android.widget.Toast
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mavi.vpn.data.InstalledApp
import com.mavi.vpn.includeSplitTunnelSelectionIsValid
import com.mavi.vpn.ui.components.MaviCard
import com.mavi.vpn.ui.components.MaviDivider
import com.mavi.vpn.ui.components.PrimaryButton
import com.mavi.vpn.ui.components.ScreenHeader
import com.mavi.vpn.ui.components.drawableToBitmap
import com.mavi.vpn.ui.components.toImageBitmap
import com.mavi.vpn.ui.theme.MaviMono
import com.mavi.vpn.ui.theme.MaviShapes
import com.mavi.vpn.ui.theme.MaviTheme
import com.mavi.vpn.viewmodel.VpnViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@Composable
fun SplitTunnelingScreen(
    viewModel: VpnViewModel,
    onBack: () -> Unit,
    onSave: (String, String) -> Unit,
) {
    val context = LocalContext.current
    val T = MaviTheme.colors
    val initialMode by viewModel.splitMode.collectAsState()
    val initialSelection by viewModel.splitPackages.collectAsState()
    var mode by remember { mutableStateOf(initialMode) }
    val selectedPackages = remember {
        mutableStateListOf<String>().apply {
            addAll(initialSelection.split(",").map { it.trim() }.filter { it.isNotEmpty() })
        }
    }
    var apps by remember { mutableStateOf<List<InstalledApp>>(emptyList()) }
    var isLoading by remember { mutableStateOf(true) }

    LaunchedEffect(Unit) {
        apps = loadLaunchableApps(context.packageManager)
        isLoading = false
    }

    fun save() {
        if (!includeSplitTunnelSelectionIsValid(mode, selectedPackages)) {
            Toast.makeText(context, "Select at least one app for include split tunneling.", Toast.LENGTH_SHORT).show()
            return
        }
        onSave(mode, selectedPackages.joinToString(","))
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(T.bg)
            .statusBarsPadding()
            .navigationBarsPadding()
            .padding(horizontal = 20.dp, vertical = 8.dp),
    ) {
        ScreenHeader(
            title = "Split tunneling",
            subtitle = "Choose which apps use the VPN",
            onBack = onBack,
        )

        ModeSelector(mode = mode, onSelect = { mode = it })

        Spacer(modifier = Modifier.height(12.dp))
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = if (mode == "include") "Only selected apps will use VPN." else "Selected apps will bypass VPN.",
                style = MaterialTheme.typography.bodyMedium,
                color = T.mute,
                modifier = Modifier.weight(1f),
            )
            Text(
                text = "${selectedPackages.size} selected",
                style = MaterialTheme.typography.labelMedium,
                color = T.accent,
            )
        }
        Spacer(modifier = Modifier.height(12.dp))

        if (isLoading) {
            Box(modifier = Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = T.accent)
            }
        } else {
            MaviCard(modifier = Modifier.weight(1f)) {
                LazyColumn {
                    itemsIndexed(apps, key = { _, app -> app.packageName }) { index, app ->
                        if (index > 0) MaviDivider()
                        AppRow(
                            app = app,
                            selected = selectedPackages.contains(app.packageName),
                            onToggle = { checked ->
                                if (checked) {
                                    if (!selectedPackages.contains(app.packageName)) selectedPackages.add(app.packageName)
                                } else {
                                    selectedPackages.remove(app.packageName)
                                }
                            },
                        )
                    }
                }
            }
        }

        PrimaryButton(
            text = "Save selection",
            onClick = ::save,
            modifier = Modifier.fillMaxWidth().padding(vertical = 12.dp),
        )
    }
}

@Composable
private fun ModeSelector(
    mode: String,
    onSelect: (String) -> Unit,
) {
    val T = MaviTheme.colors
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(MaviShapes.control))
            .background(T.subtle)
            .padding(3.dp),
    ) {
        listOf("exclude" to "Exclude selected", "include" to "Include selected").forEach { (id, label) ->
            val selected = mode == id
            Box(
                modifier = Modifier
                    .weight(1f)
                    .clip(RoundedCornerShape(8.dp))
                    .background(if (selected) T.surface else Color.Transparent)
                    .selectable(selected = selected, role = Role.RadioButton, onClick = { onSelect(id) })
                    .padding(vertical = 10.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = label,
                    style = MaterialTheme.typography.labelMedium,
                    color = if (selected) T.ink else T.mute,
                )
            }
        }
    }
}

@Composable
private fun AppRow(
    app: InstalledApp,
    selected: Boolean,
    onToggle: (Boolean) -> Unit,
) {
    val T = MaviTheme.colors
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .toggleable(value = selected, role = Role.Checkbox, onValueChange = onToggle)
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (app.icon != null) {
            Image(bitmap = app.icon, contentDescription = null, modifier = Modifier.size(36.dp))
        } else {
            Box(modifier = Modifier.size(36.dp).background(T.subtle, CircleShape))
        }
        Spacer(modifier = Modifier.width(14.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = app.name,
                style = MaterialTheme.typography.bodyLarge,
                fontWeight = FontWeight.Medium,
                color = T.ink,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                text = app.packageName,
                fontFamily = MaviMono,
                fontSize = 12.sp,
                color = T.mute,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Checkbox(
            checked = selected,
            onCheckedChange = null,
            colors = CheckboxDefaults.colors(
                checkedColor = T.accentSolid,
                uncheckedColor = T.lineStrong,
                checkmarkColor = T.onAccent,
            ),
        )
    }
}

private suspend fun loadLaunchableApps(packageManager: PackageManager): List<InstalledApp> =
    withContext(Dispatchers.IO) {
        val launcherIntent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val launchableActivities = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            packageManager.queryIntentActivities(launcherIntent, PackageManager.ResolveInfoFlags.of(0))
        } else {
            @Suppress("DEPRECATION")
            packageManager.queryIntentActivities(launcherIntent, 0)
        }
        launchableActivities.mapNotNull { activity ->
            val activityInfo = activity.activityInfo ?: return@mapNotNull null
            val appInfo = activityInfo.applicationInfo ?: return@mapNotNull null
            val isSystem = (appInfo.flags and ApplicationInfo.FLAG_SYSTEM) != 0
            val isUpdatedSystem = (appInfo.flags and ApplicationInfo.FLAG_UPDATED_SYSTEM_APP) != 0
            if (isSystem && !isUpdatedSystem) return@mapNotNull null

            InstalledApp(
                name = appInfo.loadLabel(packageManager).toString(),
                packageName = activityInfo.packageName,
                icon = drawableToBitmap(appInfo.loadIcon(packageManager))?.toImageBitmap(),
            )
        }.distinctBy { it.packageName }.sortedBy { it.name.lowercase() }
    }
