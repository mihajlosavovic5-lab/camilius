package com.ai_auto_reply_mail.app.twa;

import android.os.Bundle;
import android.view.KeyEvent;
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
    // mobile/app.js's website-download fallback) has no Play listing
    // to diff against, so getAppUpdateInfo() harmlessly resolves to
    // UPDATE_NOT_AVAILABLE there instead of erroring.
    //
    // Created in onCreate(), not as a field initializer - AppUpdateManagerFactory.create()
    // needs a real Context (it calls getApplicationContext() internally), but field
    // initializers run in the constructor, before the system has attached the Activity's
    // base Context. Doing this eagerly here crashed every launch with a NullPointerException.
    AppUpdateManager appUpdateManager;

    // Backs OverlayStatePlugin - whether mobile/app.js currently has a popup/sheet/
    // lightbox open (see that plugin's own comment for the full reasoning). Plugin calls
    // in Capacitor run on a background executor by default, not the UI thread that
    // dispatchKeyEvent() (below) reads this from - volatile is enough to make each
    // write visible to that read (a plain boolean, not a multi-step update, so there's
    // no larger critical section to protect, just the single flag's visibility).
    volatile boolean overlayOpen = false;

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
        appUpdateManager = AppUpdateManagerFactory.create(this);

        // Must run before super.onCreate() - that's where BridgeActivity actually builds
        // the plugin bridge (see its load()), so a plugin registered any later than this
        // never makes it in and capacitorPlugins.PlayUpdate (mobile/app.js) is undefined.
        registerPlugin(PlayUpdatePlugin.class);
        registerPlugin(OverlayStatePlugin.class);

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

    // Lets a popup/sheet/lightbox (mobile/app.js's closeTopmostOverlay(), tracked here
    // via OverlayStatePlugin/overlayOpen) close on a hardware back press without waiting
    // for Capacitor's own backButton JS event, which only fires from onBackPressed() -
    // the framework only invokes that once a key event bubbles all the way up unconsumed.
    //
    // This override only actually gets a chance to see BACK before the WebView does when
    // nothing focused and editable is showing the keyboard at that moment. For as long as
    // a focused, editable DOM element IS showing the keyboard, the WebView's own key
    // handling claims BACK first regardless of this override - the normal Android
    // behavior for any focused editable view, confirmed by extensive on-device testing.
    // Several attempts at defeating that (blurring the field on popup-open so nothing
    // claims BACK for the keyboard, then forcing the IME to stay visibly open through
    // WindowInsetsController so the visitor wouldn't notice the blur) all ran into the
    // same wall: Android's own back-closes-keyboard handling fired anyway, apparently
    // keyed off the IME merely being visible rather than off any view actually having
    // focus, and forcing it back open afterward just produced a confusing "closes then
    // silently reopens" side effect with no way to make it reliable. Left as the plain
    // version below instead: while a focused field is showing the keyboard, the first
    // back press behaves like normal Android (closes the keyboard) and a second closes
    // the popup - the same tradeoff most Android apps with a keyboard-aware popup make.
    // Once no keyboard is in the way, returning true here for BACK - only while
    // overlayOpen is true - consumes the event at the very top, before the WebView ever
    // sees it, and closes the popup directly via a plain evaluateJavascript() call
    // instead. Every other back press (overlayOpen false) falls through to
    // super.dispatchKeyEvent() completely unchanged, so ordinary app navigation is
    // untouched.
    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        if (overlayOpen && event.getKeyCode() == KeyEvent.KEYCODE_BACK) {
            if (event.getAction() == KeyEvent.ACTION_UP && getBridge() != null && getBridge().getWebView() != null) {
                getBridge().getWebView().evaluateJavascript(
                    "window.closeTopmostOverlay && window.closeTopmostOverlay();", null);
            }
            return true;
        }
        return super.dispatchKeyEvent(event);
    }
}
