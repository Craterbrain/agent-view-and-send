package app.avs;

import android.app.BroadcastOptions;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ResolveInfo;
import android.os.Build;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.List;
import java.util.UUID;

/**
 * Alerts through UnifiedPush: the ntfy app (the "distributor") keeps the one connection for every app on the phone and hands this app its messages as
 * broadcasts, so AVS itself holds no connection in the background. Spec: https://unifiedpush.org/developers/spec/android/
 * Until the PC has been given the push address, the old ntfy stream (ListenService) keeps running, so no alert is missed on the way over.
 */
final class Push {
    static final String REGISTER = "org.unifiedpush.android.distributor.REGISTER";
    static final String ACK = "org.unifiedpush.android.distributor.MESSAGE_ACK";
    private Push() { }

    static SharedPreferences prefs(Context c) { return c.getSharedPreferences("avs", Context.MODE_PRIVATE); }

    /** The app that distributes pushes (ntfy if it is there), or "". */
    static String distributor(Context c) {
        try {
            List<ResolveInfo> l = c.getPackageManager().queryBroadcastReceivers(new Intent(REGISTER), 0);
            String first = "";
            for (ResolveInfo r : l) {
                String p = r.activityInfo.packageName;
                if (p.equals("io.heckel.ntfy")) return p;
                if (first.isEmpty()) first = p;
            }
            return first;
        } catch (Exception e) { return ""; }
    }

    static boolean active(Context c) {
        SharedPreferences p = prefs(c);
        return !p.getString("endpoint", "").isEmpty() && p.getBoolean("endpointSent", false);
    }

    /** Called whenever the app starts: (re)register with the distributor, and keep the fallback listener until the PC has the address. */
    static void ensure(Context c) {
        register(c);
        syncAsync(c);
        try {
            if (active(c)) c.stopService(new Intent(c, ListenService.class));
            else c.startForegroundService(new Intent(c, ListenService.class));
        } catch (Exception ignored) { }
    }

    static void register(Context c) {
        String d = distributor(c);
        if (d.isEmpty()) return;
        SharedPreferences p = prefs(c);
        String token = p.getString("pushToken", "");
        if (token.isEmpty()) { token = UUID.randomUUID().toString(); p.edit().putString("pushToken", token).apply(); }
        Intent i = new Intent(REGISTER).setPackage(d).putExtra("token", token).putExtra("application", c.getPackageName()).putExtra("message", "AVS alerts")
            .putExtra("pi", PendingIntent.getBroadcast(c, 0, new Intent().setPackage("org.unifiedpush.dummy_app"), PendingIntent.FLAG_IMMUTABLE));
        try {
            if (Build.VERSION.SDK_INT >= 34) {
                BroadcastOptions o = BroadcastOptions.makeBasic();
                o.setShareIdentityEnabled(true);
                c.sendBroadcast(i, null, o.toBundle());
            } else c.sendBroadcast(i);
        } catch (Exception ignored) { }
    }

    static void ack(Context c, Intent in) {
        String id = in.getStringExtra("id"), token = in.getStringExtra("token");
        if (id == null || token == null) return;
        String d = distributor(c);
        if (d.isEmpty()) return;
        c.sendBroadcast(new Intent(ACK).setPackage(d).putExtra("token", token).putExtra("id", id));
    }

    /** Give the PC the push address (when it can be reached) so it sends alerts there. Does nothing when it already has it. */
    static void syncAsync(Context c) {
        final Context app = c.getApplicationContext();
        SharedPreferences p = prefs(app);
        if (p.getString("endpoint", "").isEmpty() || p.getBoolean("endpointSent", false) || p.getString("token", "").isEmpty()) return;
        new Thread(() -> {
            String where = reach(p);
            if (where.isEmpty()) return;
            if (post(where, p.getString("token", ""), p.getString("endpoint", ""))) {
                p.edit().putBoolean("endpointSent", true).apply();
                try { app.stopService(new Intent(app, ListenService.class)); } catch (Exception ignored) { }
            }
        }).start();
    }

    /** Tell the PC to go back to the ntfy topic (the push address stopped working). */
    static void forgetAsync(Context c) {
        final SharedPreferences p = prefs(c);
        final String token = p.getString("token", "");
        p.edit().remove("endpoint").putBoolean("endpointSent", false).apply();
        new Thread(() -> { String where = reach(p); if (!where.isEmpty()) post(where, token, ""); }).start();
    }

    private static boolean post(String base, String token, String endpoint) {
        try {
            HttpURLConnection h = (HttpURLConnection) new URL(base + "/api/push").openConnection();
            h.setConnectTimeout(8000); h.setReadTimeout(10000); h.setRequestMethod("POST"); h.setDoOutput(true);
            h.setRequestProperty("Cookie", "avs=" + token); h.setRequestProperty("Content-Type", "application/json");
            try (OutputStream o = h.getOutputStream()) { o.write(("{\"endpoint\":\"" + endpoint.replace("\\", "\\\\").replace("\"", "\\\"") + "\"}").getBytes("UTF-8")); }
            return h.getResponseCode() == 200;
        } catch (Exception e) { return false; }
    }

    private static boolean ping(String url, int ms) {
        try {
            HttpURLConnection h = (HttpURLConnection) new URL(url + "/api/ping").openConnection();
            h.setConnectTimeout(ms); h.setReadTimeout(ms);
            try (InputStream in = h.getInputStream()) { return h.getResponseCode() == 200 && new String(in.readAllBytes()).contains("\"avs\""); }
        } catch (Exception e) { return false; }
    }

    /** The PC's address: the home network first, then the relay address that was last posted to the ntfy topic. */
    private static String reach(SharedPreferences p) {
        String lan = p.getString("lan", "");
        if (!lan.isEmpty() && ping(lan, 1500)) return lan;
        String ntfy = p.getString("ntfy", ""), topic = p.getString("topic", "");
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
            return !last.isEmpty() && ping(last, 8000) ? last : "";
        } catch (Exception e) { return ""; }
    }
}
