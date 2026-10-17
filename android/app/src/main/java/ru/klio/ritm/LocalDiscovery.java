package ru.klio.ritm;

import org.json.JSONObject;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** Replies must prove knowledge of the saved pairing secret before receiving tasks. */
final class LocalDiscovery {
    static String proof(String token,String nonce,String server,int port) throws Exception {
        Mac mac=Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(token.getBytes(StandardCharsets.UTF_8),"HmacSHA256"));
        byte[] bytes=mac.doFinal((nonce+"|"+server+"|"+port).getBytes(StandardCharsets.UTF_8));
        StringBuilder result=new StringBuilder();
        for(byte value:bytes) result.append(String.format(Locale.ROOT,"%02x",value&255));
        return result.toString();
    }
    static boolean valid(JSONObject reply,String token,String nonce,String server) throws Exception {
        int port=reply.optInt("port",0);
        return port>0 && port<=65535 && "ritm-discover-v1".equals(reply.optString("service"))
            && nonce.equals(reply.optString("nonce")) && server.equals(reply.optString("server_id"))
            && MessageDigest.isEqual(proof(token,nonce,server,port).getBytes(StandardCharsets.UTF_8),
                reply.optString("proof").getBytes(StandardCharsets.UTF_8));
    }
    static String find(DatagramSocket socket,String server,String token,int port) throws Exception {
        String nonce=UUID.randomUUID().toString().replace("-","");
        byte[] request=new JSONObject().put("service","ritm-discover-v1").put("server_id",server)
            .put("nonce",nonce).toString().getBytes(StandardCharsets.UTF_8);
        Set<InetAddress> broadcasts=new HashSet<>();
        broadcasts.add(InetAddress.getByName("255.255.255.255"));
        Enumeration<NetworkInterface> interfaces=NetworkInterface.getNetworkInterfaces();
        while(interfaces!=null && interfaces.hasMoreElements()) {
            for(InterfaceAddress address:interfaces.nextElement().getInterfaceAddresses())
                if(address.getBroadcast()!=null) broadcasts.add(address.getBroadcast());
        }
        socket.setBroadcast(true);
        for(InetAddress target:broadcasts) {
            try {socket.send(new DatagramPacket(request,request.length,target,port));} catch(java.io.IOException ignored) {}
        }
        long until=System.nanoTime()+1_500_000_000L;
        while(System.nanoTime()<until) {
            socket.setSoTimeout(Math.max(1,(int)((until-System.nanoTime())/1_000_000L)));
            DatagramPacket packet=new DatagramPacket(new byte[2048],2048);
            try {
                socket.receive(packet);
                if(!(packet.getAddress() instanceof Inet4Address) || !packet.getAddress().isSiteLocalAddress()) continue;
                JSONObject reply=new JSONObject(new String(packet.getData(),0,packet.getLength(),StandardCharsets.UTF_8));
                if(valid(reply,token,nonce,server)) return "http://"+packet.getAddress().getHostAddress()+":"+reply.getInt("port");
            } catch(SocketTimeoutException timeout) {break;}
            catch(org.json.JSONException ignored) {}
        }
        return null;
    }
}
