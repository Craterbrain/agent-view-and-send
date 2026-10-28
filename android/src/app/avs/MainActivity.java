package app.avs;

import android.app.ActionBar;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.OpenableColumns;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.Menu;
import android.view.MenuItem;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/** AVS: a thin shell around the web app the PC serves. It pairs once (link from `avs pair`), finds the PC at home or through the
 *  relay (a tunnel address that the PC posts to a secret ntfy topic), shows the app in a WebView, and sends shared files to the PC. */
public class MainActivity extends Activity {
    private SharedPreferences prefs;
    private WebView web;
    private LinearLayout pairView;
    private TextView status;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private ValueCallback<Uri[]> chooser;
    private String base = "";
    private boolean resolving = false;
    private TextView barMenu, barTitle, barPos;       // the action bar: hamburger, the page's title, its position
    private String barText = "AVS", barUrl = "";
    private static final int PICK = 7;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = getSharedPreferences("avs", MODE_PRIVATE);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        web.setLayoutParams(new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        web.setWebViewClient(new WebViewClient() {
            @Override public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
                if (r.isForMainFrame()) { base = ""; connect(); }
            }
            @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                String u = r.getUrl().toString();
                if (!base.isEmpty() && u.startsWith(base)) return false;
                startActivity(new Intent(Intent.ACTION_VIEW, r.getUrl()));
                return true;
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb, FileChooserParams p) {
                if (chooser != null) chooser.onReceiveValue(null);
                chooser = cb;
                Intent i = new Intent(Intent.ACTION_GET_CONTENT).setType("*/*").addCategory(Intent.CATEGORY_OPENABLE).putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                try { startActivityForResult(Intent.createChooser(i, "Send to your agent"), PICK); } catch (Exception e) { chooser = null; return false; }
                return true;
            }
        });
        web.addJavascriptInterface(new Object() {
            // Only a file name from the PC's own outbox is accepted, never a web address; the system still asks the person to confirm.
            @android.webkit.JavascriptInterface public void install(String name) { ui.post(() -> installApk(name)); }
            // The page tells the bar what it shows: its title and position, whether it has a contents menu, and the address for "Open in browser".
            @android.webkit.JavascriptInterface public void bar(String title, String pos, boolean nav, String url) { ui.post(() -> setBar(title, pos, nav, url)); }
        }, "AVSApp");
        web.setDownloadListener((url, ua, cd, mime, len) -> {
            DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
            r.addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url));
            r.setMimeType(mime);
            r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, URLUtil.guessFileName(url, cd, mime));
            ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(r);
            Toast.makeText(this, "Downloading…", Toast.LENGTH_SHORT).show();
        });
        status = new TextView(this);
        status.setPadding(40, 24, 40, 24);
        status.setVisibility(View.GONE);
        pairView = pairingScreen();
        root.addView(status);
        root.addView(pairView);
        root.addView(web);
        setContentView(root);
        root.setFitsSystemWindows(true);
        buildBar();
        handle(getIntent());
    }

    /** The action bar is one row: a hamburger (opens the page's contents), the page's title and position, then the usual three-dot menu.
     *  Pulling down on the row refreshes the page, the same pull the web app used to take on its own title bar. */
    private void buildBar() {
        ActionBar ab = getActionBar();
        if (ab == null) return;
        Context c = ab.getThemedContext();
        TypedValue tv = new TypedValue();
        c.getTheme().resolveAttribute(android.R.attr.textColorPrimary, tv, true);
        final int ink = tv.resourceId != 0 ? c.getResources().getColorStateList(tv.resourceId, c.getTheme()).getDefaultColor() : tv.data;
        final float d = getResources().getDisplayMetrics().density;
        LinearLayout row = new LinearLayout(c);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        barMenu = new TextView(c);
        barMenu.setText("\u2630");
        barMenu.setTextSize(22);
        barMenu.setTextColor(ink);
        barMenu.setGravity(Gravity.CENTER);
        barMenu.setVisibility(View.GONE);
        barMenu.setLayoutParams(new LinearLayout.LayoutParams((int) (48 * d), ViewGroup.LayoutParams.MATCH_PARENT));
        barTitle = new TextView(c);
        barTitle.setText(barText);
        barTitle.setTextSize(18);
        barTitle.setTextColor(ink);
        barTitle.setTypeface(barTitle.getTypeface(), android.graphics.Typeface.BOLD);
        barTitle.setSingleLine(true);
        barTitle.setEllipsize(TextUtils.TruncateAt.END);
        barTitle.setPadding((int) (4 * d), 0, (int) (4 * d), 0);
        barTitle.setLayoutParams(new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        barPos = new TextView(c);
        barPos.setTextSize(13);
        barPos.setTextColor(ink);
        barPos.setAlpha(0.6f);
        barPos.setPadding(0, 0, (int) (6 * d), 0);
        row.addView(barMenu);
        row.addView(barTitle);
        row.addView(barPos);
        final float[] down = new float[3];                  // x, y, how far it has been pulled
        row.setOnTouchListener((v, e) -> {
            switch (e.getActionMasked()) {
                case MotionEvent.ACTION_DOWN: down[0] = e.getX(); down[1] = e.getY(); down[2] = 0; return true;
                case MotionEvent.ACTION_MOVE:
                    down[2] = e.getY() - down[1];
                    barTitle.setText(down[2] >= 80 * d ? "Release to refresh" : barText);
                    return true;
                case MotionEvent.ACTION_UP:
                    barTitle.setText(barText);
                    if (down[2] >= 80 * d) web.evaluateJavascript("window.avsRefresh&&avsRefresh()", null);
                    else if (Math.abs(down[2]) < 12 * d && down[0] < 52 * d && barMenu.getVisibility() == View.VISIBLE)
                        web.evaluateJavascript("window.avsMenu&&avsMenu()", null);
                    return true;
                case MotionEvent.ACTION_CANCEL: barTitle.setText(barText); return true;
            }
            return false;
        });
        ab.setDisplayShowTitleEnabled(false);
        ab.setDisplayShowHomeEnabled(false);
        ab.setDisplayShowCustomEnabled(true);
        ab.setCustomView(row, new ActionBar.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
    }

    private void setBar(String title, String pos, boolean nav, String url) {
        barText = title == null || title.isEmpty() ? "AVS" : title;
        barTitle.setText(barText);
        barPos.setText(pos == null ? "" : pos);
        barMenu.setVisibility(nav ? View.VISIBLE : View.GONE);
        barUrl = url == null ? "" : url;
    }

    /** Downloads an .apk from the PC's outbox straight into a package-installer session and lets the system ask to confirm. */
    private void installApk(String name) {
        if (base.isEmpty() || name == null || !name.toLowerCase().endsWith(".apk") || name.contains("/")) return;
        if (!getPackageManager().canRequestPackageInstalls()) {
            Toast.makeText(this, "Allow AVS to install apps, then tap Install again", Toast.LENGTH_LONG).show();
            startActivity(new Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + getPackageName())));
            return;
        }
        Toast.makeText(this, "Downloading " + name + "…", Toast.LENGTH_SHORT).show();
        final String b = base;
        new Thread(() -> {
            android.content.pm.PackageInstaller pi = getPackageManager().getPackageInstaller();
            int id = -1;
            try {
                String url = b + "/api/files/outbox/" + URLEncoder.encode(name, "UTF-8").replace("+", "%20");
                HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                String cookie = CookieManager.getInstance().getCookie(url);
                if (cookie != null) c.setRequestProperty("Cookie", cookie);
                if (c.getResponseCode() != 200) throw new java.io.IOException("HTTP " + c.getResponseCode());
                id = pi.createSession(new android.content.pm.PackageInstaller.SessionParams(android.content.pm.PackageInstaller.SessionParams.MODE_FULL_INSTALL));
                try (android.content.pm.PackageInstaller.Session ses = pi.openSession(id)) {
                    try (InputStream in = c.getInputStream(); OutputStream out = ses.openWrite("avs.apk", 0, c.getContentLengthLong())) {
                        byte[] buf = new byte[1 << 16]; int n;
                        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                        ses.fsync(out);
                    }
                    android.app.PendingIntent done = android.app.PendingIntent.getBroadcast(this, id,
                        new Intent(this, InstallResult.class).setPackage(getPackageName()), android.app.PendingIntent.FLAG_MUTABLE | android.app.PendingIntent.FLAG_UPDATE_CURRENT);
                    ses.commit(done.getIntentSender());
                }
            } catch (Exception e) {
                if (id >= 0) try { pi.abandonSession(id); } catch (Exception ignored) {}
                ui.post(() -> Toast.makeText(this, "Could not install: " + e.getMessage(), Toast.LENGTH_LONG).show());
            }
        }).start();
    }

    @Override protected void onNewIntent(Intent i) { super.onNewIntent(i); setIntent(i); handle(i); }
    // In the background the app holds no connection to the PC: the page stops polling, the heartbeat stops, and alerts reach the phone through
    // ListenService (the ntfy stream), which is the only thing left running. Coming back checks the PC at once.
    @Override protected void onResume() {
        super.onResume();
        web.onResume(); web.resumeTimers();
        if (paired() && base.isEmpty()) connect();
        else if (paired() && !base.isEmpty()) { final String b = base; new Thread(() -> { if (!ping(b, 5000)) ui.post(() -> { if (b.equals(base)) { base = ""; connect(); } }); }).start(); }
        heartbeat();
    }

    @Override protected void onPause() {
        ui.removeCallbacks(beat);
        web.pauseTimers(); web.onPause();
        super.onPause();
    }

    private boolean paired() { return !prefs.getString("token", "").isEmpty(); }

    private LinearLayout pairingScreen() {
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setPadding(48, 64, 48, 48);
        TextView t = new TextView(this);
        t.setText("Pair with your PC\n\nOn the PC run:  avs pair\nThen copy the avs://pair… link here (or open it on this phone).");
        t.setTextSize(16);
        EditText e = new EditText(this);
        e.setHint("avs://pair?…"); e.setSingleLine(false);
        Button paste = new Button(this); paste.setText("Paste from clipboard");
        paste.setOnClickListener(v -> {
            ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
            ClipData d = cm.getPrimaryClip();
            if (d != null && d.getItemCount() > 0 && d.getItemAt(0).getText() != null) e.setText(d.getItemAt(0).getText());
        });
        Button go = new Button(this); go.setText("Pair");
        go.setOnClickListener(v -> { if (!pair(e.getText().toString().trim())) Toast.makeText(this, "That is not an AVS pairing link.", Toast.LENGTH_LONG).show(); });
        l.addView(t); l.addView(e); l.addView(paste); l.addView(go);
        return l;
    }

    private String tab = "";

    private void handle(Intent i) {
        if (i == null) return;
        tab = nz(i.getStringExtra("tab"));
        if (!tab.isEmpty() && !base.isEmpty()) { web.loadUrl(base + "/?t=" + Uri.encode(prefs.getString("token", "")) + "#" + tab); tab = ""; }
        if (Intent.ACTION_VIEW.equals(i.getAction()) && i.getData() != null) { pair(i.getData().toString()); return; }
        if (Intent.ACTION_SEND.equals(i.getAction()) || Intent.ACTION_SEND_MULTIPLE.equals(i.getAction())) {
            ArrayList<Uri> uris = new ArrayList<>();
            if (Intent.ACTION_SEND.equals(i.getAction())) { Uri u = i.getParcelableExtra(Intent.EXTRA_STREAM, Uri.class); if (u != null) uris.add(u); }
            else { ArrayList<Uri> m = i.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri.class); if (m != null) uris.addAll(m); }
            if (!uris.isEmpty()) share(uris);
        }
        showMode();
    }

    private boolean pair(String link) {
        Uri u;
        try { u = Uri.parse(link); } catch (Exception e) { return false; }
        if (!"avs".equals(u.getScheme()) || u.getQueryParameter("token") == null) return false;
        prefs.edit().putString("token", u.getQueryParameter("token")).putString("lan", nz(u.getQueryParameter("lan")))
            .putString("topic", nz(u.getQueryParameter("topic"))).putString("ntfy", nz(u.getQueryParameter("ntfy"))).apply();
        base = "";
        showMode();
        Toast.makeText(this, "Paired", Toast.LENGTH_SHORT).show();
        return true;
    }

    private static String nz(String s) { return s == null ? "" : s; }

    private void listen() {
        if (!paired()) return;
        if (checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{android.Manifest.permission.POST_NOTIFICATIONS}, 9);          // the service starts when this is answered
            return;
        }
        Push.ensure(this);
    }

    @Override public void onRequestPermissionsResult(int code, String[] perms, int[] res) { Push.ensure(this); }

    private void showMode() {
        boolean p = paired();
        if (p) listen();
        pairView.setVisibility(p ? View.GONE : View.VISIBLE);
        web.setVisibility(p ? View.VISIBLE : View.GONE);
        if (p && base.isEmpty()) connect();
    }

    // ---- finding the PC: the home network first, then the relay address from ntfy -----------------------------------------
    private void connect() {
        if (resolving || !paired()) return;
        resolving = true;
        say("Looking for your PC…");
        new Thread(() -> {
            String found = "";
            String lan = prefs.getString("lan", "");
            if (!lan.isEmpty() && ping(lan, 1500)) found = lan;
            if (found.isEmpty()) {
                String relay = latestRelay();
                if (!relay.isEmpty() && ping(relay, 8000)) found = relay;
            }
            final String f = found;
            ui.post(() -> {
                resolving = false;
                if (f.isEmpty()) { say("Cannot reach your PC. Is it on and is AVS running? Menu → Reconnect."); return; }
                base = f;
                Push.syncAsync(this);
                say(null);
                web.loadUrl(f + "/?t=" + Uri.encode(prefs.getString("token", "")) + (tab.isEmpty() ? "" : "#" + tab));
                tab = "";
            });
        }).start();
    }

    private void say(String s) { status.setText(s == null ? "" : s); status.setVisibility(s == null ? View.GONE : View.VISIBLE); }

    private boolean ping(String url, int ms) {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(url + "/api/ping").openConnection();
            c.setConnectTimeout(ms); c.setReadTimeout(ms);
            try (InputStream in = c.getInputStream()) { return new String(in.readAllBytes()).contains("\"avs\""); }
        } catch (Exception e) { return false; }
    }

    private String latestRelay() {
        String ntfy = prefs.getString("ntfy", ""), topic = prefs.getString("topic", "");
        if (ntfy.isEmpty() || topic.isEmpty()) return "";
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(ntfy + "/" + topic + "/json?poll=1&since=12h").openConnection();
            c.setConnectTimeout(8000); c.setReadTimeout(8000);
            String last = "";
            try (InputStream in = c.getInputStream()) {
                for (String line : new String(in.readAllBytes()).split("\n")) {
                    java.util.regex.Matcher m = java.util.regex.Pattern.compile("\"message\":\"(https://[a-z0-9-]+\\.trycloudflare\\.com)\"").matcher(line);
                    if (m.find()) last = m.group(1);
                }
            }
            return last;
        } catch (Exception e) { return ""; }
    }

    /** Every 20 s while the app is on screen (not in the background): if the PC stopped answering (the relay address changed, the Wi-Fi changed), find it again. */
    private final Runnable beat = new Runnable() {
        @Override public void run() {
            if (paired() && !base.isEmpty() && !resolving) {
                final String b = base;
                new Thread(() -> { if (!ping(b, 5000)) ui.post(() -> { if (b.equals(base)) { base = ""; connect(); } }); }).start();
            }
            heartbeat();
        }
    };

    private void heartbeat() { ui.removeCallbacks(beat); ui.postDelayed(beat, 20000); }

    // ---- sending files shared from other apps ------------------------------------------------------------------------------
    private void share(List<Uri> uris) {
        if (!paired()) { Toast.makeText(this, "Pair with your PC first, then share again.", Toast.LENGTH_LONG).show(); return; }
        Toast.makeText(this, "Sending " + uris.size() + " file(s)…", Toast.LENGTH_SHORT).show();
        new Thread(() -> {
            String msg;
            try {
                String where = base;
                if (where.isEmpty()) {
                    String lan = prefs.getString("lan", "");
                    where = (!lan.isEmpty() && ping(lan, 1500)) ? lan : latestRelay();
                }
                if (where.isEmpty()) throw new Exception("PC not reachable");
                upload(where, uris);
                msg = "Sent to your PC";
            } catch (Exception e) { msg = "Not sent: " + e.getMessage(); }
            final String m = msg;
            ui.post(() -> Toast.makeText(this, m, Toast.LENGTH_LONG).show());
        }).start();
    }

    private void upload(String where, List<Uri> uris) throws Exception {
        String bd = "----avs" + UUID.randomUUID();
        HttpURLConnection c = (HttpURLConnection) new URL(where + "/api/upload").openConnection();
        c.setDoOutput(true); c.setRequestMethod("POST"); c.setChunkedStreamingMode(1 << 16);
        c.setConnectTimeout(10000); c.setReadTimeout(120000);
        c.setRequestProperty("Cookie", "avs=" + prefs.getString("token", ""));
        c.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + bd);
        try (OutputStream out = c.getOutputStream()) {
            for (Uri u : uris) {
                String name = "file";
                try (Cursor q = getContentResolver().query(u, null, null, null, null)) {
                    if (q != null && q.moveToFirst()) { int i = q.getColumnIndex(OpenableColumns.DISPLAY_NAME); if (i >= 0) name = q.getString(i); }
                }
                String type = getContentResolver().getType(u);
                out.write(("--" + bd + "\r\nContent-Disposition: form-data; name=\"files\"; filename=\"" + name.replace("\"", "_") + "\"\r\nContent-Type: "
                    + (type == null ? "application/octet-stream" : type) + "\r\n\r\n").getBytes());
                try (InputStream in = getContentResolver().openInputStream(u)) { in.transferTo(out); }
                out.write("\r\n".getBytes());
            }
            out.write(("--" + bd + "--\r\n").getBytes());
        }
        if (c.getResponseCode() != 200) throw new Exception("PC said " + c.getResponseCode());
    }

    // ---- the web app asks for files; menu ------------------------------------------------------------------------------------
    @Override protected void onActivityResult(int req, int res, Intent d) {
        if (req != PICK || chooser == null) return;
        Uri[] r = null;
        if (res == RESULT_OK && d != null) {
            if (d.getClipData() != null) { r = new Uri[d.getClipData().getItemCount()]; for (int i = 0; i < r.length; i++) r[i] = d.getClipData().getItemAt(i).getUri(); }
            else if (d.getData() != null) r = new Uri[]{d.getData()};
        }
        chooser.onReceiveValue(r);
        chooser = null;
    }

    @Override public boolean onCreateOptionsMenu(Menu m) { m.add(0, 1, 0, "Reconnect"); m.add(0, 2, 1, "Pair again"); m.add(0, 3, 2, "Open in browser"); return true; }
    @Override public boolean onPrepareOptionsMenu(Menu m) { m.findItem(3).setVisible(!barUrl.isEmpty()); return true; }
    @Override public boolean onOptionsItemSelected(MenuItem i) {
        if (i.getItemId() == 1) { base = ""; connect(); }
        if (i.getItemId() == 3 && !barUrl.isEmpty()) startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(barUrl)));
        if (i.getItemId() == 2) new AlertDialog.Builder(this).setMessage("Forget this PC and pair again?").setPositiveButton("Forget", (d, w) -> {
            prefs.edit().clear().apply(); base = ""; web.loadUrl("about:blank"); setBar("AVS", "", false, ""); showMode(); }).setNegativeButton("Cancel", null).show();
        return true;
    }
    @Override public void onBackPressed() { if (web.getVisibility() == View.VISIBLE && web.canGoBack()) web.goBack(); else moveTaskToBack(true); }
}
