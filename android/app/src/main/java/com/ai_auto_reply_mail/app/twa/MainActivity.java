package com.ai_auto_reply_mail.app.twa;

import android.os.Bundle;
import androidx.core.splashscreen.SplashScreen;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        // Must run before super.onCreate() - that's where BridgeActivity actually builds
        // the plugin bridge (see its load()), so a plugin registered any later than this
        // never makes it in and capacitorPlugins.ApkInstaller (mobile/app.js) is undefined.
        registerPlugin(ApkInstallerPlugin.class);

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
    }
}
