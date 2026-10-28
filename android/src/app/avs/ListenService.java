package app.avs;

import android.app.Notification;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.os.IBinder;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

/** Keeps one connection open to the secret ntfy topic and turns the PC's "avs-event" messages into notifications. */
public class ListenService extends Service {
    private volatile boolean running;
    private Thread worker;

    @Override public IBinder onBind(Intent i) { return null; }

    @Override public int onStartCommand(Intent i, int flags, int id) {
        Alerts.channels(this);
        Notification n = new Notification.Builder(this, "listen").setSmallIcon(R.drawable.ic_launcher).setContentTitle("AVS is listening")
            .setContentIntent(Alerts.open(this, "view")).build();
        startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        if (!running) {
            running = true;
            worker = new Thread(this::loop);
            worker.start();
        }
        return START_STICKY;
    }

    @Override public void onDestroy() { running = false; if (worker != null) worker.interrupt(); super.onDestroy(); }

    private void loop() {
        SharedPreferences p = getSharedPreferences("avs", MODE_PRIVATE);
        long wait = 2000;
        while (running) {
            String ntfy = p.getString("ntfy", ""), topic = p.getString("topic", "");
            if (ntfy.isEmpty() || topic.isEmpty()) { sleep(30000); continue; }
            // Catch up from the last message seen; the first time, only what happens from now on.
            String since = p.getString("lastId", "");
            if (since.isEmpty()) since = String.valueOf(System.currentTimeMillis() / 1000);
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(ntfy + "/" + topic + "/json?since=" + since).openConnection();
                c.setConnectTimeout(10000); c.setReadTimeout(120000);          // ntfy sends a keepalive about every 45 s
                try (BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream()))) {
                    String line;
                    while (running && (line = r.readLine()) != null) { wait = 2000; handle(p, line); }
                }
            } catch (Exception e) { /* offline or timed out: try again */ }
            sleep(wait);
            wait = Math.min(wait * 2, 60000);
        }
    }

    private void handle(SharedPreferences p, String line) {
        try {
            JSONObject m = new JSONObject(line);
            if (!"message".equals(m.optString("event"))) return;
            p.edit().putString("lastId", m.optString("id")).apply();
            JSONArray tags = m.optJSONArray("tags");
            boolean ours = false;
            for (int i = 0; tags != null && i < tags.length(); i++) if ("avs-event".equals(tags.optString(i))) ours = true;
            if (!ours) return;
            Alerts.show(this, m.optString("title", "AVS"), m.optString("message"));
        } catch (Exception ignored) { }
    }

    private void sleep(long ms) { try { Thread.sleep(ms); } catch (InterruptedException ignored) { } }
}
