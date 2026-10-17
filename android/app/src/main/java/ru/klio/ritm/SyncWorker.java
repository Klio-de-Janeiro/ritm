package ru.klio.ritm;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import androidx.work.*;
import org.json.*;
import java.net.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;

/** Обмен запускается также без WebView: очередь доступна после перезагрузки. */
public class SyncWorker extends Worker {
    private static final Object NETWORK_LOCK=new Object();
    public SyncWorker(Context context,WorkerParameters parameters) {super(context,parameters);}
    public static void schedule(Context context) {
        Constraints constraints=new Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build();
        WorkManager.getInstance(context).enqueueUniquePeriodicWork("ritm-periodic",ExistingPeriodicWorkPolicy.KEEP,
            new PeriodicWorkRequest.Builder(SyncWorker.class,15,TimeUnit.MINUTES).setConstraints(constraints).build());
    }
    public static void now(Context context) {
        WorkManager.getInstance(context).enqueueUniqueWork("ritm-now",ExistingWorkPolicy.KEEP,
            new OneTimeWorkRequest.Builder(SyncWorker.class).setConstraints(new Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED).build()).setBackoffCriteria(BackoffPolicy.EXPONENTIAL,30,TimeUnit.SECONDS).build());
    }
    /** Разрешены только частные IPv4; без DNS, редиректов и произвольного URL. */
    public static String address(String text) throws Exception {
        URI uri=new URI(text.trim());String host=uri.getHost();
        if(!"http".equals(uri.getScheme()) || host==null || !host.matches("[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+") || uri.getUserInfo()!=null || uri.getQuery()!=null || uri.getFragment()!=null || !(uri.getPath().isEmpty() || uri.getPath().equals("/"))) throw new IllegalArgumentException("Нужен адрес вида http://192.168.1.10:8769");
        InetAddress ip=InetAddress.getByName(host);
        if(!(ip instanceof Inet4Address) || !ip.isSiteLocalAddress()) throw new IllegalArgumentException("Разрешены только адреса домашней сети: 10.x, 172.16–31.x, 192.168.x.");
        int port=uri.getPort()==-1?8769:uri.getPort();
        if(port<1 || port>65535) throw new IllegalArgumentException("Некорректный порт.");
        return "http://"+ip.getHostAddress()+":"+port;
    }
    @Override public Result doWork() {
        synchronized(NETWORK_LOCK) {
            Storage storage=new Storage(getApplicationContext());
            try {
                for(int page=0;page<50;page++) {
                    JSONObject initial; synchronized(Storage.LOCK) {initial=storage.read();}
                    JSONObject connection=initial.getJSONObject("connection");
                    if(connection.optString("address").isEmpty()) return Result.success();
                    JSONArray queued=initial.getJSONArray("pending"), sent=new JSONArray();
                    for(int i=0;i<Math.min(100,queued.length());i++) sent.put(queued.get(i));
                    JSONObject request=new JSONObject().put("protocol",2).put("server_id",initial.optString("server_id"))
                        .put("operations",sent);
                    Network network=localNetwork(getApplicationContext());
                    JSONObject target=new JSONObject(connection.toString());
                    if(page==0 && !initial.optString("server_id").isEmpty()) {
                        try(DatagramSocket socket=new DatagramSocket(null)) {
                            if(network!=null) network.bindSocket(socket);
                            socket.bind(new InetSocketAddress(0));
                            int port=new URI(address(connection.getString("address"))).getPort();
                            String found=LocalDiscovery.find(socket,initial.getString("server_id"),connection.getString("token"),port);
                            if(found!=null) target.put("address",address(found));
                        } catch(Exception ignored) { /* The saved address remains usable without broadcast. */ }
                    }
                    JSONObject response=exchange(target,request,network);
                    synchronized(Storage.LOCK) {
                        JSONObject fresh=storage.read();
                        // Подключение могло быть отключено или изменено, пока ответ шёл по сети.
                        if(!fresh.getJSONObject("connection").toString().equals(connection.toString())) return Result.success();
                        Model.accept(fresh,response,sent);
                        fresh.put("connection",target);storage.write(fresh);
                    }
                    Reminders.refresh(getApplicationContext());
                    if(queued.length()<=100) break;
                }
                return Result.success();
            } catch(Exception error) {
                String message=error.getMessage();
                storage.error(message==null?"Ноутбук недоступен. Изменения сохранены на телефоне.":message);
                return getRunAttemptCount()<3?Result.retry():Result.failure();
            }
        }
    }
    static Network localNetwork(Context context) {
        ConnectivityManager manager=context.getSystemService(ConnectivityManager.class);
        for(Network network:manager.getAllNetworks()) {
            NetworkCapabilities caps=manager.getNetworkCapabilities(network);
            if(caps!=null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
                && (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) || caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))) return network;
        }
        return null;
    }
    static JSONObject exchange(JSONObject connection,JSONObject body,Network network) throws Exception {
        URL url=new URL(address(connection.getString("address"))+"/sync/v1");
        HttpURLConnection http=(HttpURLConnection)(network==null?url.openConnection(Proxy.NO_PROXY):network.openConnection(url,Proxy.NO_PROXY));
        http.setConnectTimeout(7000);http.setReadTimeout(20000);http.setInstanceFollowRedirects(false);
        http.setRequestMethod("POST");http.setDoOutput(true);
        http.setRequestProperty("Content-Type","application/json");
        http.setRequestProperty("Authorization","Bearer "+connection.getString("token"));
        byte[] bytes=body.toString().getBytes(StandardCharsets.UTF_8);http.setFixedLengthStreamingMode(bytes.length);
        try {
            try(OutputStream output=http.getOutputStream()) {output.write(bytes);}
            int code=http.getResponseCode();
            InputStream input=code==200?http.getInputStream():http.getErrorStream();
            if(input==null) throw new IOException("Ноутбук ответил с ошибкой "+code);
            ByteArrayOutputStream result=new ByteArrayOutputStream();
            try(InputStream source=input) {
                byte[] buffer=new byte[8192];int count;
                while((count=source.read(buffer))!=-1) {
                    result.write(buffer,0,count);
                    if(result.size()>24_000_000) throw new IOException("Снимок ноутбука слишком большой.");
                }
            }
            JSONObject response=new JSONObject(result.toString(StandardCharsets.UTF_8.name()));
            if(code!=200) throw new IOException(response.optString("error","Ошибка синхронизации: "+code));
            return response;
        } finally {http.disconnect();}
    }
}
