package ru.klio.ritm;

import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class LocalDiscoveryTest {
    @Test public void rejectsUnknownPeersReplaysAndChangedPort() throws Exception {
        String token="saved-pairing-secret",nonce="a".repeat(32),server="my-laptop";
        JSONObject reply=new JSONObject().put("service","ritm-discover-v1").put("server_id",server)
            .put("nonce",nonce).put("port",8769).put("proof",LocalDiscovery.proof(token,nonce,server,8769));
        assertTrue(LocalDiscovery.valid(reply,token,nonce,server));
        assertFalse(LocalDiscovery.valid(reply,"other-secret",nonce,server));
        assertFalse(LocalDiscovery.valid(reply,token,"b".repeat(32),server));
        assertFalse(LocalDiscovery.valid(reply,token,nonce,"other-laptop"));
        reply.put("port",8770);
        assertFalse(LocalDiscovery.valid(reply,token,nonce,server));
    }
}
