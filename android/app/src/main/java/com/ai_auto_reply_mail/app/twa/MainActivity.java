package com.ai_auto_reply_mail.app.twa;

import android.os.Bundle;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.IntentSenderRequest;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.core.splashscreen.SplashScreen;
import com.getcapacitor.BridgeActivity;
import com.google.android.play.core.appupdate.AppUpdateInfo;
import com.google.android.play.core.appupdate.AppUpdateManager;
import com.google.android.play.core.appupdate.AppUpdateManagerFactory;
import com.google.android.play.core.appupdate.AppUpdateOptions;
import com.google.android.play.core.install.InstallStateUpdatedListener;
import com.google.android.play.core.install.model.AppUpdateType;
import com.google.android.play.core.install.model.InstallStatus;
import com.google.android.play.core.install.model.UpdateAvailability;

public class MainActivity extends BridgeActivity {

    // Backs PlayUpdatePlugin (mobile/app.js's apk-update-check) - package-private so
    // that plugin can read appUpdateInfo state and drive updates through it without
    // duplicating the Activity Result plumbing below, which only a real Activity/
    // ComponentActivity (not a Plugin) can own. Only ever finds anything for an install
    // that actually came from the Play Store - a sideload (still possible; see
    // ApkInstallerPlugin/mobile/app.js's website-download fallback) has no Play listing
    // to diff against, so getAppUpdateInfo() harmlessly resolves to
    // UPDATE_NOT_AVAILABLE there instead of erroring.
    final AppUpdateManager appUpdateManager = AppUpdateManagerFactory.create(this);

    // Must be registered before the Activity reaches STARTED - a field initializer
    // (runs during construction, well before onCreate) is the documented-safe place for
    // this. Handles both FLEXIBLE and IMMEDIATE update flows; a cancelled/failed
    // request just leaves the app on its current version, nothing to recover here -
    // PlayUpdatePlugin's next checkForUpdate() call re-offers it.
    final ActivityResultLauncher<IntentSenderRequest> playUpdateLauncher = registerForActivityResult(
        new ActivityResultContracts.StartIntentSenderForResult(),
        result -> {}
    );

    // Reports a FLEXIBLE update's background download landing - PlayUpdatePlugin has no
    // open PluginCall to resolve at that point (startUpdate() already returned once the
    // download merely started), so this reaches the JS side directly instead, the same
    // way Capacitor's own plugin events do.
    private final InstallStateUpdatedListener installStateListener = state -> {
        if (state.installStatus() == InstallStatus.DOWNLOADED && getBridge() != null) {
            getBridge().triggerWindowJSEvent("playUpdateDownloaded");
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        // Must run before super.onCreate() - that's where BridgeActivity actually builds
        // the plugin bridge (see its load()), so a plugin registered any later than this
        // never makes it in and capacitorPlugins.ApkInstaller/PlayUpdate (mobile/app.js)
        // are undefined.
        registerPlugin(ApkInstallerPlugin.class);
        registerPlugin(PlayUpdatePlugin.class);

        SplashScreen splashScreen = SplashScreen.installSplashScreen(this);
        super.onCreate(savedInstanceState);

        // Keeps the native splash up a beat past its default "first frame drawn"
        // dismissal - long enough for Android to finish dispatching the real
        // WindowInsets (status bar height) to the WebView. Without this, the WebView's
        // first screen can reveal itself (see mobile/app.js's waitForLayoutSettle,
        // which covers the same gap from the web side) before that dispatch lands, with
        // mobile/app.css's --safe-top still at its 0px fallback - then the instant the
        // real inset arrives, every screen's top padding (built on --safe-top) grows,
        // shoving the whole screen down by the status bar's height right as the splash
        // disappears. Only ever happens on a genuine cold start; a warm resume already
        // has the inset from the first time around.
        final long minDismissAt = System.currentTimeMillis() + 200;
        splashScreen.setKeepOnScreenCondition(() -> System.currentTimeMillis() < minDismissAt);

        appUpdateManager.registerListener(installStateListener);
    }

    @Override
    public void onResume() {
        super.onResume();
        // Resumes an IMMEDIATE update the visitor backed out of mid-flow - Play
        // requires that one to be re-shown on the next resume, it can't just be
        // dropped the way a FLEXIBLE request can. PlayUpdatePlugin's own
        // checkForUpdate()/startUpdate() calls (driven from mobile/app.js on cold
        // start) cover the initial offer; this only covers resuming one already
        // in progress.
        appUpdateManager.getAppUpdateInfo().addOnSuccessListener(info -> {
            if (info.updateAvailability() == UpdateAvailability.DEVELOPER_TRIGGERED_UPDATE_IN_PROGRESS) {
                startPlayUpdateFlow(info, AppUpdateType.IMMEDIATE);
            }
        });
    }

    @Override
    public void onDestroy() {
        appUpdateManager.unregisterListener(installStateListener);
        super.onDestroy();
    }

    // Package-private - called from PlayUpdatePlugin, which resolved the AppUpdateInfo
    // itself first (needs to check isUpdateTypeAllowed(updateType) before this).
    void startPlayUpdateFlow(AppUpdateInfo info, int updateType) {
        try {
            appUpdateManager.startUpdateFlowForResult(
                info,
                playUpdateLauncher,
                AppUpdateOptions.newBuilder(updateType).build()
            );
        } catch (Exception e) {
            // Nothing to recover - PlayUpdatePlugin's next checkForUpdate() call
            // re-offers it.
        }
    }
}
