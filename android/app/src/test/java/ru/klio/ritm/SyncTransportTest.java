package ru.klio.ritm;

import org.json.JSONObject;
import org.junit.Test;
import java.net.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;
import static org.junit.Assert.*;

public class SyncTransportTest {
    @Test public void localTunnelUsesAuthenticatedPostAndReadsFramedResponse() throws Exception {
        try(ServerSocket server=new ServerSocket(0)) {
            ExecutorService executor=Executors.newSingleThreadExecutor();
            try {
                Future<String> request=executor.submit(()->{
                    try(Socket socket=server.accept()) {
                        socket.setSoTimeout(3000);
                        BufferedReader input=new BufferedReader(new InputStreamReader(socket.getInputStream(),StandardCharsets.UTF_8));
                        StringBuilder headers=new StringBuilder();String line;int length=0;
                        while((line=input.readLine())!=null&&!line.isEmpty()) {
                            headers.append(line).append('\n');
                            if(line.toLowerCase(java.util.Locale.ROOT).startsWith("content-length:")) length=Integer.parseInt(line.substring(15).trim());
                        }
                        char[] body=new char[length];int count=0;
                        while(count<length) {int read=input.read(body,count,length-count);if(read<0) throw new EOFException();count+=read;}
                        byte[] response="{\"protocol\":2}".getBytes(StandardCharsets.UTF_8);
                        socket.getOutputStream().write(("HTTP/1.1 200 OK\r\nContent-Length: "+response.length+"\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
                        socket.getOutputStream().write(response);
                        return headers.toString()+new String(body);
                    }
                });
                JSONObject response=SyncWorker.httpExchange(new URL("http://127.0.0.1:"+server.getLocalPort()+"/sync/v1"),new JSONObject().put("token","test-pairing-secret"),new JSONObject().put("protocol",2),null,1000);
                assertEquals(2,response.getInt("protocol"));
                String sent=request.get(3,TimeUnit.SECONDS);
                assertTrue(sent.contains("POST /sync/v1"));assertTrue(sent.contains("Authorization: Bearer test-pairing-secret"));
                assertTrue(sent.contains("\"protocol\":2"));
            } finally {executor.shutdownNow();}
        }
    }
}
