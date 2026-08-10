package com.ai_auto_reply_mail.app.twa;

import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

// Lets mobile/app.js tell MainActivity whether a popup/sheet/lightbox (any .lang-modal
// instance, or #image-lightbox) is currently open, so MainActivity.dispatchKeyEvent()
// can decide whether IT should handle the hardware back button itself, before the
// WebView gets a chance to - see that method's own comment for why that matters (in
// short: a focused text field's on-screen keyboard otherwise eats the very first back
// press to hide itself, so a popup open behind/above it needed a second press to close,
// and closing it that way also left the keyboard toggled off as a side effect neither
// press was actually meant to cause).
//
// A boolean flag on MainActivity rather than this plugin querying the DOM itself -
// dispatchKeyEvent() needs a synchronous, always-current answer to decide whether to
// consume the very event it's in the middle of handling; a plugin call is async
// (round-trips through the JS bridge), which can't fit into that same call. app.js's
// MutationObserver (see closeTopmostOverlay's own comment) keeps this call, and so the
// flag, in sync with the DOM's actual open/closed state on every change.
@CapacitorPlugin(name = "OverlayState")
public class OverlayStatePlugin extends Plugin {

    @PluginMethod
    public void setOverlayOpen(PluginCall call) {
        boolean open = call.getBoolean("open", false);
        boolean forceKeyboard = call.getBoolean("forceKeyboard", false);
        MainActivity activity = (MainActivity) getActivity();
        activity.overlayOpen = open;
        if (open) {
            // Plugin methods run on Capacitor's own background executor, not the UI
            // thread (see the class comment above) - forceShowIme()/keepImeVisibleIfShowing()
            // touch the Window/View hierarchy via WindowInsetsControllerCompat, which
            // Android requires to happen on the UI thread only. Calling it directly from
            // here (as an earlier version of this fix did) threw
            // CalledFromWrongThreadException and crashed the app the instant the sheet
            // opened - runOnUiThread() is what's missing.
            //
            // forceKeyboard (app.js's openAttachSheet(), called directly and synchronously
            // right alongside its own blur() - not this generic, MutationObserver-delayed
            // path) means the caller already knows, from its own synchronous DOM check
            // taken the instant before it blurred the field, that the keyboard was up -
            // so this unconditionally shows it back rather than re-querying current
            // WindowInsets state (see MainActivity.keepImeVisibleIfShowing()'s own comment
            // for why that query is a race that on-device testing showed losing
            // intermittently: correct on a popup's first open in a session, silently
            // failing to restore the keyboard on a later one). Every other caller (the
            // generic syncOverlayOpenState observer, for every other popup/sheet/
            // lightbox) has no such synchronous signal to go on, so it keeps using the
            // safer, insets-state-checked fallback.
            activity.runOnUiThread(() -> {
                if (forceKeyboard) {
                    activity.forceShowIme();
                } else {
                    activity.keepImeVisibleIfShowing();
                }
            });
        }
        call.resolve();
    }
}
