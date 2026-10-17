package ru.klio.ritm;

import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class AppearanceTest {
    @Test public void validatesSavedThemeAndRejectsInvalidValues() {
        Appearance.validate("background_color","#ffffff");
        Appearance.validate("glow_color",JSONObject.NULL);
        Appearance.validate("glow_color","#aabbcc");
        Appearance.validate("glow_enabled",false);
        Appearance.validate("border_radius",24);
        Appearance.validate("background_brightness",100);
        Appearance.validate("theme","light");
        for(Object[] pair:new Object[][]{{"border_radius",25},{"border_radius",true},
                {"background_color","url(fake)"},{"theme","invalid"},{"glow_enabled",1},
                {"unexpected",0},{"accent_color",JSONObject.NULL}}) {
            try {Appearance.validate((String)pair[0],pair[1]);fail("Invalid theme accepted");}
            catch(IllegalArgumentException expected) {}
        }
    }
}
