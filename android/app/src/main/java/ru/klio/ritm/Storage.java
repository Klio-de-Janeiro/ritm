package ru.klio.ritm;

import android.content.Context;
import android.util.AtomicFile;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;

/** AtomicFile + общий монитор: UI и Worker никогда не перезаписывают чужую правку. */
public final class Storage {
    public static final Object LOCK=new Object();
    private final AtomicFile file;
    public Storage(Context context) {file=new AtomicFile(new File(context.getFilesDir(),"ritm-state.json"));}
    public JSONObject read() throws Exception {
        synchronized(LOCK) {
            if(!file.getBaseFile().exists() && !new File(file.getBaseFile()+".bak").exists()) return Model.empty();
            try(InputStream input=file.openRead()) {
                ByteArrayOutputStream bytes=new ByteArrayOutputStream();byte[] buffer=new byte[8192];int count;
                while((count=input.read(buffer))!=-1) {
                    bytes.write(buffer,0,count);
                    if(bytes.size()>32_000_000) throw new IOException("Хранилище слишком большое.");
                }
                JSONObject state=new JSONObject(bytes.toString(StandardCharsets.UTF_8.name()));
                if(state.getInt("schema")!=1) throw new IOException("Нужна более новая версия Ритма.");
                return state;
            }
        }
    }
    public void write(JSONObject data) throws Exception {
        synchronized(LOCK) {
            byte[] bytes=data.toString().getBytes(StandardCharsets.UTF_8);
            if(bytes.length>32_000_000) throw new IOException("Хранилище заполнено. Сделай резервную копию.");
            FileOutputStream output=null;
            try {output=file.startWrite();output.write(bytes);file.finishWrite(output);}
            catch(Exception error) {if(output!=null) file.failWrite(output);throw error;}
        }
    }
    public void error(String message) {
        synchronized(LOCK) {try {JSONObject data=read();data.put("sync_error",message);write(data);} catch(Exception ignored) {}}
    }
}
