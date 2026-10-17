package ru.klio.ritm;

import org.json.JSONObject;
import java.util.Arrays;

/** Shared desktop theme keys; phone preferences remain local. */
final class Appearance {
    static void validate(String key,Object value) {
        if(Arrays.asList("accent_color","background_color","panel_color","sidebar_color","field_color",
                "text_color","muted_color","border_color","glow_color").contains(key)) {
            if(value==JSONObject.NULL && !key.equals("accent_color")) return;
            if(value instanceof String && ((String)value).matches("#[0-9a-fA-F]{6}")) return;
        } else if(Arrays.asList("palette_enabled","glow_enabled","gradients","borders").contains(key)) {
            if(value instanceof Boolean) return;
        } else if(key.equals("theme")) {
            if(Arrays.asList("neon","midnight","light").contains(value)) return;
        } else {
            int max;
            switch(key) {
                case "palette_hue": max=360;break;
                case "border_radius": max=24;break;
                case "palette_saturation": case "background_brightness": case "neon_intensity": max=100;break;
                default: throw new IllegalArgumentException("Неизвестная настройка.");
            }
            if(value instanceof Integer && (int)value>=0 && (int)value<=max) return;
        }
        throw new IllegalArgumentException("Некорректная настройка оформления: "+key);
    }
}
