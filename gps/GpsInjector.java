import android.location.Location;
import android.location.LocationManager;
import android.location.provider.ProviderProperties;
import android.os.Looper;
import android.os.SystemClock;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.File;

/**
 * GpsInjector - root/shell-side GPS injection daemon for the onn 7 Core head unit.
 *
 * `cmd location providers set-test-provider-location` supports only
 * --location/--accuracy/--time: it CANNOT carry speed or bearing. So speed
 * widgets and map heading read zero. This injects fully-populated Location
 * objects (speed, bearing, accuracy, elapsedRealtimeNanos) instead.
 *
 * Reads lines from a FIFO:  lat,lon[,acc[,speed[,bearing]]]
 * Injects into gps + network + fused so the framework's fused provider cannot
 * out-vote an injected gps-only fix with a GMS network estimate.
 *
 * PITFALLS (both cost real debugging time):
 *  - ActivityThread.systemMain() creates a Handler, so main() MUST call
 *    Looper.prepare() first or it dies with
 *    "Can't create handler inside thread that has not called Looper.prepare()".
 *  - Do NOT call LocationManager.getService() - it does not exist. Get the
 *    service via ActivityThread.systemMain().getApplication().getSystemService().
 *  - main() must not declare checked exceptions (no `throws Exception`), or ART
 *    will not accept it as an entry point and the process dies with SIGKILL.
 */
public class GpsInjector {

    static final String FIXFILE = "/data/local/tmp/gps_fix";
    static final String[] PROVIDERS = {"gps", "network", "fused"};

    static LocationManager lm;

    /**
     * (Re)register the three test providers.
     *
     * Self-healing on purpose: another process (e.g. `cmd location
     * add-test-provider` run as the shell uid) can evict these, and a mock
     * provider is tied to the registering process. Re-asserting periodically
     * means the mock providers always come back instead of silently vanishing.
     */
    static void register(boolean fresh) {
        for (String p : PROVIDERS) {
            try {
                ProviderProperties props = new ProviderProperties.Builder()
                        .setPowerUsage(ProviderProperties.POWER_USAGE_LOW)
                        .setAccuracy(ProviderProperties.ACCURACY_FINE)
                        .setHasAltitudeSupport(true)
                        .setHasSpeedSupport(true)
                        .setHasBearingSupport(true)
                        .setHasSatelliteRequirement("gps".equals(p))
                        .setHasNetworkRequirement("network".equals(p))
                        .build();
                if (fresh) { try { lm.removeTestProvider(p); } catch (Throwable ignored) {} }
                lm.addTestProvider(p, props);
            } catch (Throwable t) {
                // already present -> fine
            }
            try { lm.setTestProviderEnabled(p, true); } catch (Throwable ignored) {}
        }
    }

    public static void main(String[] args) {
        try {
            Looper.prepare();   // required before ActivityThread.systemMain()
            Class<?> at = Class.forName("android.app.ActivityThread");
            Object ctx = at.getMethod("systemMain").invoke(null);
            Object app = at.getMethod("getApplication").invoke(ctx);
            lm = (LocationManager) ((android.content.Context) app)
                    .getSystemService(android.content.Context.LOCATION_SERVICE);
            if (lm == null) { System.out.println("ERR no LocationManager"); return; }

            register(true);
            System.out.println("READY providers=" + String.join(",", PROVIDERS));
            System.out.flush();
        } catch (Throwable t) {
            System.out.println("FATAL " + t);
            Throwable c = t.getCause(); if (c != null) System.out.println("CAUSE " + c);
            System.out.flush();
            return;
        }

        // Poll a plain file for the latest fix. A FIFO was tried first and
        // proved fragile: read/write races and EOF churn when a writer closes.
        // At 1 Hz updates, polling at 5 Hz is trivial and cannot deadlock.
        String last = "";
        int tick = 0;
        while (true) {
            try {
                BufferedReader r = new BufferedReader(new FileReader(FIXFILE));
                String line = r.readLine();
                r.close();
                if (line != null) {
                    line = line.trim();
                    if (!line.isEmpty() && !line.equals(last)) {
                        last = line;
                        inject(line);
                    }
                }
            } catch (Throwable t) {
                // file may not exist yet
            }
            try { Thread.sleep(200); } catch (InterruptedException ignored) {}
            if (++tick % 10 == 0) register(false);   // self-heal every ~2s
        }
    }

    static void inject(String line) {
        try {
            String[] f = line.split(",");
            if (f.length < 2) return;
            double lat = Double.parseDouble(f[0].trim());
            double lon = Double.parseDouble(f[1].trim());
            float acc   = f.length > 2 && !f[2].trim().isEmpty() ? Float.parseFloat(f[2].trim()) : 5f;
            float speed = f.length > 3 && !f[3].trim().isEmpty() ? Float.parseFloat(f[3].trim()) : 0f;
            float bear  = f.length > 4 && !f[4].trim().isEmpty() ? Float.parseFloat(f[4].trim()) : 0f;

            long now = SystemClock.elapsedRealtimeNanos();
            for (String p : PROVIDERS) {
                Location l = new Location(p);
                l.setLatitude(lat);
                l.setLongitude(lon);
                l.setAccuracy(acc);
                l.setTime(System.currentTimeMillis());
                l.setElapsedRealtimeNanos(now);
                if (speed > 0f) { l.setSpeed(speed); l.setBearing(bear); }
                try { lm.setTestProviderLocation(p, l); }
                catch (Throwable t) { /* provider may be mid-recreate */ }
            }
            System.out.println("OK " + lat + "," + lon + " acc=" + acc
                    + " speed=" + speed + " bearing=" + bear);
            System.out.flush();
        } catch (Throwable t) {
            System.out.println("ERR " + t);
            System.out.flush();
        }
    }
}

