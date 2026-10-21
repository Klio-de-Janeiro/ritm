package ru.klio.ritm;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.LinkProperties;
import android.net.RouteInfo;
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
        WorkManager.getInstance(context).cancelUniqueWork("ritm-now"); // Retire jobs that required Wi-Fi before USB support.
        WorkManager.getInstance(context).enqueueUniquePeriodicWork("ritm-periodic",ExistingPeriodicWorkPolicy.UPDATE,
            new PeriodicWorkRequest.Builder(SyncWorker.class,15,TimeUnit.MINUTES).build());
    }
    public static void now(Context context) {
        WorkManager.getInstance(context).enqueueUniqueWork("ritm-now-local",ExistingWorkPolicy.KEEP,
            new OneTimeWorkRequest.Builder(SyncWorker.class).setBackoffCriteria(BackoffPolicy.EXPONENTIAL,30,TimeUnit.SECONDS).build());
    }
    /** Разрешены только частные IPv4; без DNS, редиректов и произвольного URL. */
    public static String address(String text) throws Exception {
        URI uri=new URI(text.trim());String host=uri.getHost();
        if(!"http".equals(uri.getScheme()) || host==null || !host.matches("[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+") || uri.getUserInfo()!=null || uri.getQuery()!=null || uri.getFragment()!=null || !(uri.getPath().isEmpty() || uri.getPath().equals("/"))) throw new IllegalArgumentException("Нужен адрес вида http://192.168.1.10:8769");
        InetAddress ip=InetAddress.getByName(host);
        int port=uri.getPort()==-1?8769:uri.getPort();
        if(port<1 || port>65535) throw new IllegalArgumentException("Некорректный порт.");
        boolean usb=host.equals("127.0.0.1") && port==8769;
        if(!(ip instanceof Inet4Address) || !(ip.isSiteLocalAddress() || usb)) throw new IllegalArgumentException("Нужен адрес локальной сети или USB: http://127.0.0.1:8769");
        return "http://"+ip.getHostAddress()+":"+port;
    }
    @Override public Result doWork() {
        synchronized(NETWORK_LOCK) {
            Storage storage=new Storage(getApplicationContext());
            try {
                for(int page=0;page<50;page++) {
                    JSONObject initial,device;
                    synchronized(Storage.LOCK) {
                        initial=storage.read();
                        String previous=initial.optString("device_id");
                        device=Model.device(initial,android.os.Build.MODEL);
                        if(!previous.equals(initial.getString("device_id"))) storage.write(initial);
                    }
                    JSONObject connection=initial.getJSONObject("connection");
                    if(connection.optString("address").isEmpty()) return Result.success();
                    JSONArray queued=initial.getJSONArray("pending"), sent=new JSONArray();
                    for(int i=0;i<Math.min(100,queued.length());i++) sent.put(queued.get(i));
                    JSONObject request=new JSONObject().put("protocol",2).put("server_id",initial.optString("server_id"))
                        .put("operations",sent).put("device",device);
                    JSONObject target=new JSONObject(connection.toString());
                    JSONObject response=null;
                    String transport="LAN";
                    // ADB reverse is local to this phone. Pairing identity and token are unchanged.
                    if(!initial.optString("server_id").isEmpty() || connection.getString("address").equals("http://127.0.0.1:8769")) {
                        try { response=exchangeUsb(connection,request);transport="USB"; }
                        catch(IOException ignored) { /* No cable/tunnel: keep using the saved LAN pairing. */ }
                    }
                    Network network=response==null?localNetwork(getApplicationContext(),target.getString("address")):null;
                    if(response==null && page==0 && !initial.optString("server_id").isEmpty()) {
                        try(DatagramSocket socket=new DatagramSocket(null)) {
                            if(network!=null) network.bindSocket(socket);
                            socket.bind(new InetSocketAddress(0));
                            int port=new URI(address(connection.getString("address"))).getPort();
                            String found=LocalDiscovery.find(socket,initial.getString("server_id"),connection.getString("token"),port);
                            if(found!=null) target.put("address",address(found));
                        } catch(Exception ignored) { /* The saved address remains usable without broadcast. */ }
                    }
                    if(response==null) {
                        network=localNetwork(getApplicationContext(),target.getString("address"));
                        try {response=exchange(target,request,network);}
                        catch(IOException error) {throw new IOException("Нет соединения с ноутбуком: "+target.getString("address")+". Подключи USB-кабель с разрешённой отладкой или общую локальную сеть. Изменения сохранены на телефоне.",error);}
                    }
                    boolean remaining;
                    synchronized(Storage.LOCK) {
                        JSONObject fresh=storage.read();
                        // Подключение могло быть отключено или изменено, пока ответ шёл по сети.
                        if(!fresh.getJSONObject("connection").toString().equals(connection.toString())) return Result.success();
                        Model.accept(fresh,response,sent);
                        fresh.put("sync_transport",transport);
                        fresh.put("connection",target);storage.write(fresh);
                        remaining=fresh.getJSONArray("pending").length()>0;
                        android.util.Log.i("RitmSync","transport="+transport+" sent="+sent.length()+" pending="+fresh.getJSONArray("pending").length()+" conflicts="+fresh.getJSONArray("conflicts").length()+" tasks="+fresh.getJSONArray("tasks").length());
                        JSONArray conflicts=fresh.getJSONArray("conflicts");
                        for(int i=0;i<conflicts.length();i++) {
                            JSONObject conflict=conflicts.getJSONObject(i);
                            JSONObject operation=conflict.getJSONObject("operation");
                            android.util.Log.i("RitmSync","conflict kind="+operation.optString("kind")+" task="+operation.optString("task_id")+" date="+operation.optString("date")+" reason="+conflict.optString("reason"));
                        }
                    }
                    Reminders.refresh(getApplicationContext());
                    if(!remaining) break;
                }
                return Result.success();
            } catch(Exception error) {
                String message=error.getMessage();
                storage.error(message==null?"Ноутбук недоступен. Изменения сохранены на телефоне.":message);
                return getRunAttemptCount()<3?Result.retry():Result.failure();
            }
        }
    }
    static Network localNetwork(Context context,String address) throws Exception {
        ConnectivityManager manager=context.getSystemService(ConnectivityManager.class);
        InetAddress target=InetAddress.getByName(new URI(SyncWorker.address(address)).getHost());
        Network selected=null;int prefix=0;
        for(Network network:manager.getAllNetworks()) {
            NetworkCapabilities caps=manager.getNetworkCapabilities(network);
            if(caps!=null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
                && (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) || caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))) {
                if(target.isLoopbackAddress() && selected==null) selected=network;
                LinkProperties links=manager.getLinkProperties(network);
                if(links!=null) for(RouteInfo route:links.getRoutes()) {
                    int length=route.getDestination().getPrefixLength();
                    if(length>prefix && route.getDestination().contains(target)) {prefix=length;selected=network;}
                }
            }
        }
        return selected;
    }
    static JSONObject exchangeUsb(JSONObject connection,JSONObject body) throws Exception {
        return httpExchange(new URL("http://127.0.0.1:8769/sync/v1"),connection,body,null,1000);
    }
    static JSONObject exchange(JSONObject connection,JSONObject body,Network network) throws Exception {
        URL url=new URL(address(connection.getString("address"))+"/sync/v1");
        return httpExchange(url,connection,body,network,7000);
    }
    static JSONObject httpExchange(URL url,JSONObject connection,JSONObject body,Network network,int connectTimeout) throws Exception {
        HttpURLConnection http=(HttpURLConnection)(network==null?url.openConnection(Proxy.NO_PROXY):network.openConnection(url,Proxy.NO_PROXY));
        http.setConnectTimeout(connectTimeout);http.setReadTimeout(20000);http.setInstanceFollowRedirects(false);
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
