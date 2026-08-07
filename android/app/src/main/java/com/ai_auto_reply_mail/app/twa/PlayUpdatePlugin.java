package com.ai_auto_reply_mail.app.twa;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.google.android.play.core.install.model.AppUpdateType;
import com.google.android.play.core.install.model.InstallStatus;
import com.google.android.play.core.install.model.UpdateAvailability;

// In-app updates via Google Play, so a native-code release (see MainActivity.java's
// comment on ApkInstallerPlugin/mobile/app.js's apk-update-check for what still can't
// ship through the OTA bundle) can reach a Play Store install without the website
// download + "Install unknown apps" dance that a sideloaded install still needs -
// Play is always a trusted install source, so it never needs that per-app grant, and it
// downloads/verifies the update itself in the background.
// startUpdateFlowForResult needs an ActivityResultLauncher registered on the Activity
// itself (a Plugin isn't a ComponentActivity and can't register one of its own), so the
// actual AppUpdateManager/launcher live on MainActivity; this is a thin relay to it.
// Every method here rejects outright - rather than resolving with some "not available"
// placeholder - when the app isn't actually a Play Store install (most commonly: still
// sideloaded, or a Play install that hasn't synced its own listing yet), so
// mobile/app.js's apk-update-check can tell "nothing to offer" (this plugin's
// checkForUpdate() resolves available:false) apart from "can't ask Play at all" (any of
// these reject) and fall back to the website flow only for the latter.
@CapacitorPlugin(name = "PlayUpdate")
public class PlayUpdatePlugin extends Plugin {

    @PluginMethod
    public void checkForUpdate(PluginCall call) {
        MainActivity activity = (MainActivity) getActivity();
        activity.appUpdateManager
            .getAppUpdateInfo()
            .addOnSuccessListener(info -> {
                boolean available = info.updateAvailability() == UpdateAvailability.UPDATE_AVAILABLE;
                JSObject result = new JSObject();
                result.put("available", available);
                result.put("flexibleAllowed", available && info.isUpdateTypeAllowed(AppUpdateType.FLEXIBLE));
                result.put("immediateAllowed", available && info.isUpdateTypeAllowed(AppUpdateType.IMMEDIATE));
                result.put("downloaded", info.installStatus() == InstallStatus.DOWNLOADED);
                call.resolve(result);
            })
            .addOnFailureListener(e -> call.reject("Failed to check for update: " + e.getMessage(), e));
    }

    @PluginMethod
    public void startUpdate(PluginCall call) {
        String requestedType = call.getString("type", "FLEXIBLE");
        int updateType = "IMMEDIATE".equals(requestedType) ? AppUpdateType.IMMEDIATE : AppUpdateType.FLEXIBLE;
        MainActivity activity = (MainActivity) getActivity();
        activity.appUpdateManager
            .getAppUpdateInfo()
            .addOnSuccessListener(info -> {
                if (info.updateAvailability() != UpdateAvailability.UPDATE_AVAILABLE || !info.isUpdateTypeAllowed(updateType)) {
                    call.reject("Update not available or '" + requestedType + "' not allowed right now");
                    return;
                }
                // Only starts the flow (for FLEXIBLE: kicks off the background
                // download) - it doesn't wait for the result. A FLEXIBLE download
                // finishing is reported separately, via MainActivity's
                // InstallStateUpdatedListener firing a "playUpdateDownloaded" window
                // event; IMMEDIATE takes over the screen itself and never returns here.
                activity.startPlayUpdateFlow(info, updateType);
                call.resolve();
            })
            .addOnFailureListener(e -> call.reject("Failed to start update: " + e.getMessage(), e));
    }

    // Installs an already-downloaded FLEXIBLE update and restarts the app - only
    // meaningful once checkForUpdate() has reported downloaded:true (or the
    // "playUpdateDownloaded" window event has fired).
    @PluginMethod
    public void completeUpdate(PluginCall call) {
        MainActivity activity = (MainActivity) getActivity();
        activity.appUpdateManager.completeUpdate();
        call.resolve();
    }
}
