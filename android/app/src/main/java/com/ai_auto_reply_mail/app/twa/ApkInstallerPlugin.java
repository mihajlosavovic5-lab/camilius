package com.ai_auto_reply_mail.app.twa;

import android.content.Intent;
import android.net.Uri;
import androidx.core.content.FileProvider;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.io.File;

// Hands a native-APK update - already downloaded to disk in the background by
// mobile/app.js's apk-update-check, via @capacitor/filesystem - to Android's own
// package installer. A plain file:// Uri is blocked by FileUriExposedException on
// API 24+, so this needs the same FileProvider (see AndroidManifest.xml/file_paths.xml)
// used elsewhere, plus the REQUEST_INSTALL_PACKAGES permission declared in
// AndroidManifest.xml. Android still shows its own "Install unknown app?" confirmation
// after this - that's an OS security measure and can't be skipped from here.
@CapacitorPlugin(name = "ApkInstaller")
public class ApkInstallerPlugin extends Plugin {

    @PluginMethod
    public void installApk(PluginCall call) {
        String path = call.getString("path");
        if (path == null) {
            call.reject("Missing 'path'");
            return;
        }

        // mobile/app.js may pass either a plain filesystem path (Filesystem.downloadFile's
        // result) or a "file://" URI (Filesystem.stat's result, used for a file downloaded
        // in an earlier session) - normalize both down to a File before re-wrapping below.
        Uri parsed = Uri.parse(path);
        String filePath = "file".equals(parsed.getScheme()) ? parsed.getPath() : path;

        File file = new File(filePath);
        if (!file.exists()) {
            call.reject("File does not exist: " + filePath);
            return;
        }

        Uri contentUri = FileProvider.getUriForFile(getContext(), getContext().getPackageName() + ".fileprovider", file);

        Intent intent = new Intent(Intent.ACTION_VIEW);
        intent.setDataAndType(contentUri, "application/vnd.android.package-archive");
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);

        try {
            getContext().startActivity(intent);
            call.resolve();
        } catch (Exception e) {
            call.reject("Failed to launch installer: " + e.getMessage(), e);
        }
    }
}
