package ru.klio.ritm;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.*;
import android.content.*;
import android.net.*;
import android.view.*;
import android.webkit.*;
import android.widget.FrameLayout;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.util.*;

/** WebView содержит только ресурсы APK; мост недоступен удалённым страницам. */
public class MainActivity extends Activity {
    private WebView web;
    private ConnectivityManager.NetworkCallback networkCallback;
    private String exportData;
    private static final String ORIGIN="https://ritm.invalid";
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        FrameLayout frame=new FrameLayout(this);web=new WebView(this);
        frame.addView(web,new FrameLayout.LayoutParams(-1,-1));setContentView(frame);
        // Android 15 edge-to-edge: клавиатура, вырез и жестовая панель не перекрывают кнопки.
        frame.setOnApplyWindowInsetsListener((view,insets)-> {
            int bottom=insets.getSystemWindowInsetBottom();
            if(Build.VERSION.SDK_INT>=30) bottom=Math.max(bottom,insets.getInsets(WindowInsets.Type.ime()).bottom);
            view.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),bottom);
            return insets;
        });
        WebSettings settings=web.getSettings();settings.setJavaScriptEnabled(true);
        settings.setAllowFileAccess(false);settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSupportMultipleWindows(false);settings.setMediaPlaybackRequiresUserGesture(true);
        web.setBackgroundColor(0xff080f26);web.addJavascriptInterface(new Bridge(),"RitmNative");
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request) {return true;}
            @Override public WebResourceResponse shouldInterceptRequest(WebView view,WebResourceRequest request) {
                Uri uri=request.getUrl();String path=uri.getPath();
                if(!"https".equals(uri.getScheme()) || !"ritm.invalid".equals(uri.getHost()) || !Arrays.asList("/index.html","/mobile.css","/mobile.js","/icon.png").contains(path)) return blocked();
                try {
                    String type=path.endsWith(".html")?"text/html":path.endsWith(".css")?"text/css":path.endsWith(".js")?"application/javascript":"image/png";
                    Map<String,String> headers=new HashMap<>();
                    headers.put("Content-Security-Policy","default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'");
                    headers.put("X-Content-Type-Options","nosniff");
                    return new WebResourceResponse(type,"UTF-8",200,"OK",headers,getAssets().open(path.substring(1)));
                } catch(IOException error) {return blocked();}
            }
        });
        web.loadUrl(ORIGIN+"/index.html");
        SyncWorker.schedule(this);
        networkCallback=new ConnectivityManager.NetworkCallback() {
            @Override public void onAvailable(Network network) {SyncWorker.now(MainActivity.this);}
        };
        getSystemService(ConnectivityManager.class).registerNetworkCallback(new NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET).build(),networkCallback);
    }
    private static WebResourceResponse blocked() {return new WebResourceResponse("text/plain","UTF-8",403,"Forbidden",Collections.emptyMap(),new ByteArrayInputStream(new byte[0]));}
    @Override public void onResume() {super.onResume();SyncWorker.now(this);Reminders.refresh(this);}
    @Override public void onRequestPermissionsResult(int requestCode,String[] permissions,int[] grantResults) {
        super.onRequestPermissionsResult(requestCode,permissions,grantResults);
        if(requestCode==12) Reminders.refresh(this);
    }
    @Override public void onDestroy() {
        if(networkCallback!=null) getSystemService(ConnectivityManager.class).unregisterNetworkCallback(networkCallback);
        if(web!=null) {web.removeJavascriptInterface("RitmNative");web.destroy();}super.onDestroy();
    }
    @Override public void onBackPressed() {web.evaluateJavascript("window.ritmBack ? window.ritmBack() : false",value->{if(!"true".equals(value)) finish();});}
    public final class Bridge {
        @JavascriptInterface public String call(String encoded) {
            try {
                if(encoded==null || encoded.length()>200_000) throw new IllegalArgumentException("Слишком большой запрос.");
                JSONObject command=new JSONObject(encoded);String action=command.getString("action");
                JSONObject result=new JSONObject();Storage storage=new Storage(MainActivity.this);boolean changed=false;
                synchronized(Storage.LOCK) {
                    JSONObject data=storage.read();
                    switch(action) {
                        case "state":
                            result=Model.view(data,command.optString("date",LocalDate.now().toString()));
                            result.put("notification_permission",Reminders.permitted(MainActivity.this));break;
                        case "mutate": Model.mutate(data,command.getJSONObject("command"));changed=true;break;
                        case "connect":
                            String address=SyncWorker.address(command.getString("address"));String token=command.getString("token").trim();
                            if(!token.matches("[A-Za-z0-9_-]{32,128}")) throw new IllegalArgumentException("Вставь полный код из настроек ноутбука.");
                            data.put("connection",new JSONObject().put("address",address).put("token",token));data.put("sync_error","");changed=true;break;
                        case "disconnect": data.put("connection",new JSONObject());changed=true;break;
                        case "resolve": Model.resolve(data,command.getInt("index"),command.getBoolean("keepPhone"));changed=true;break;
                        case "settings":
                            JSONObject values=command.getJSONObject("values"),settings=data.getJSONObject("settings");
                            for(Iterator<String> it=values.keys();it.hasNext();) {
                                String key=it.next();Object value=values.get(key);
                                switch(key) {
                                    case "hue": if(!(value instanceof Integer) || (int)value<0 || (int)value>360) throw new IllegalArgumentException("Некорректный оттенок.");break;
                                    case "brightness": if(!(value instanceof Integer) || (int)value<2 || (int)value>20) throw new IllegalArgumentException("Некорректная яркость.");break;
                                    case "notifications": if(!(value instanceof Boolean)) throw new IllegalArgumentException("Некорректная настройка.");break;
                                    case "background": case "accent": if(value!=JSONObject.NULL && (!(value instanceof String) || !((String)value).matches("#[0-9a-fA-F]{6}"))) throw new IllegalArgumentException("Некорректный цвет.");break;
                                    default: throw new IllegalArgumentException("Неизвестная настройка.");
                                }
                                settings.put(key,value);
                            }
                            changed=true;break;
                        case "preview": result.put("tasks",Model.plan(command.getJSONObject("plan")));break;
                        case "import":
                            JSONArray tasks=Model.plan(command.getJSONObject("plan"));String receipt=command.getString("request_id");
                            if(!receipt.equals(data.optString("last_import"))) {
                                for(int i=0;i<tasks.length();i++) Model.mutate(data,new JSONObject().put("kind","create").put("values",tasks.get(i)));
                                data.put("last_import",receipt);changed=true;
                            }
                            result.put("created",tasks.length());break;
                        case "sync": break;
                        case "copy":
                            String text=command.getString("text");runOnUiThread(()-> getSystemService(android.content.ClipboardManager.class).setPrimaryClip(ClipData.newPlainText("Ритм",text)));break;
                        case "chatgpt": runOnUiThread(()-> {try {startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse("https://chatgpt.com/")));} catch(ActivityNotFoundException e) {new AlertDialog.Builder(MainActivity.this).setMessage("Установи браузер для открытия ChatGPT.").setPositiveButton("ОК",null).show();}});break;
                        case "permission": runOnUiThread(()-> {
                            if(Build.VERSION.SDK_INT>=33 && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)!=android.content.pm.PackageManager.PERMISSION_GRANTED) requestPermissions(new String[]{android.Manifest.permission.POST_NOTIFICATIONS},12);
                            else startActivity(new Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(android.provider.Settings.EXTRA_APP_PACKAGE,getPackageName()));
                        });break;
                        case "export":
                            JSONObject safe=Model.copy(data);safe.remove("connection");exportData=safe.toString(2);
                            runOnUiThread(()-> startActivityForResult(new Intent(Intent.ACTION_CREATE_DOCUMENT).setType("application/json").addCategory(Intent.CATEGORY_OPENABLE).putExtra(Intent.EXTRA_TITLE,"Ritm-phone-backup.json"),41));break;
                        default: throw new IllegalArgumentException("Неизвестное действие.");
                    }
                    if(changed) storage.write(data);
                }
                if(changed || action.equals("sync")) {SyncWorker.now(MainActivity.this);Reminders.refresh(MainActivity.this);}
                return new JSONObject().put("ok",true).put("data",result).toString();
            } catch(Exception error) {
                return "{\"ok\":false,\"error\":"+JSONObject.quote(error.getMessage()==null?"Не удалось сохранить изменения.":error.getMessage())+"}";
            }
        }
    }
    @Override protected void onActivityResult(int requestCode,int resultCode,Intent intent) {
        super.onActivityResult(requestCode,resultCode,intent);
        if(requestCode==41 && resultCode==RESULT_OK && intent!=null && intent.getData()!=null && exportData!=null) {
            try(OutputStream output=getContentResolver().openOutputStream(intent.getData(),"wt")) {
                if(output==null) throw new IOException("Файл недоступен.");
                output.write(exportData.getBytes(StandardCharsets.UTF_8));
            } catch(Exception error) {new AlertDialog.Builder(this).setMessage("Не удалось сохранить копию: "+error.getMessage()).setPositiveButton("ОК",null).show();}
            finally {exportData=null;}
        }
    }
}
