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
        MainActivity activity = (MainActivity) getActivity();
        activity.overlayOpen = open;
        // Only on the open transition - see MainActivity.keepImeVisibleIfShowing()'s own
        // comment for why a popup opening (app.js's openAttachSheet() blurring the compose
        // input) needs this, and why closing never does.
        if (open) {
            activity.keepImeVisibleIfShowing();
        }
        call.resolve();
    }
}
