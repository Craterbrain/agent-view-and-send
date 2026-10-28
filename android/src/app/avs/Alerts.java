package app.avs;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;

/** The notification for something the PC says happened ("File sent", "Page updated"): the same whether it came through the ntfy stream or UnifiedPush. */
final class Alerts {
    private Alerts() { }

    static PendingIntent open(Context c, String tab) {
        Intent i = new Intent(c, MainActivity.class).putExtra("tab", tab).setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(c, tab.hashCode(), i, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    static void channels(Context c) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("listen", "Connection", NotificationManager.IMPORTANCE_MIN));
        nm.createNotificationChannel(new NotificationChannel("events", "From your agent", NotificationManager.IMPORTANCE_DEFAULT));
    }

    static void show(Context c, String title, String text) {
        channels(c);
        String tab = title.startsWith("File") ? "files" : "view";
        Notification n = new Notification.Builder(c, "events").setSmallIcon(R.drawable.ic_launcher).setContentTitle(title)
            .setContentText(text).setAutoCancel(true).setContentIntent(open(c, tab)).build();
        c.getSystemService(NotificationManager.class).notify((int) (System.currentTimeMillis() & 0x7fffffff), n);
    }
}
