"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const code = fs.readFileSync(path.join(__dirname, "../ritm/web/theme.js"), "utf8");
const properties = new Map();
const storage = new Map();
const context = {
  document: {
    documentElement: {dataset: {}, style: {setProperty: (key, value) => properties.set(key, value)}},
    querySelector: () => ({setAttribute() {}}),
  },
  localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
  window: {},
};
vm.createContext(context);
vm.runInContext(code, context);
const engine = context.window.RitmTheme;

/** Сравнить фактические CSS-цвета по формуле относительной яркости. */
function contrast(a, b) {
  const brightness = hex => {
    const channels = [1, 3, 5].map(offset => parseInt(hex.slice(offset, offset + 2), 16) / 255)
      .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return channels.reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
  };
  const values = [brightness(a), brightness(b)].sort((x, y) => x - y);
  return (values[1] + 0.05) / (values[0] + 0.05);
}

for (const theme of ["neon", "midnight", "light"]) {
  for (const accent of ["#000000", "#ffffff", "#35dcf7", "#ff0000", "#00ff00", "#0000ff", "#777777", "#a08bff"]) {
    engine.apply({theme, accent_color: accent, neon_intensity: 0});
    const text = properties.get("--accent-text");
    for (const surface of ["--background", "--panel", "--sidebar", "--field"]) {
      assert.ok(contrast(text, properties.get(surface)) >= 4.5, `${theme}: ${accent} on ${surface}`);
    }
    assert.ok(contrast(accent, properties.get("--accent-foreground")) >= 4.5);
    assert.equal(properties.get("--glow-blur"), "0.0px");
    assert.equal(context.document.documentElement.dataset.theme, theme);
  }
}
engine.apply({theme: "midnight", accent_color: "#a08bff", neon_intensity: 25});
engine.apply({theme: "light", accent_color: "#ffb86b", neon_intensity: 100}, {cache: false});
assert.equal(engine.restore().theme, "midnight");
assert.equal(engine.restore().neon_intensity, 25);
storage.set("ritm-appearance-v1", '{"theme":["light"],"accent_color":["#ffffff"],"neon_intensity":true}');
assert.equal(engine.restore().theme, "neon");
assert.equal(engine.restore().accent_color, "#35dcf7");
storage.set("ritm-appearance-v1", "not-json");
assert.equal(engine.restore().theme, "neon");
context.localStorage.getItem = () => { throw new Error("Storage blocked"); };
context.localStorage.setItem = () => { throw new Error("Storage blocked"); };
assert.doesNotThrow(() => engine.restore());
console.log("Theme engine: 24 palette/accent combinations, cache, preview and invalid settings OK.");

for (const hue of [0, 60, 120, 180, 240, 300, 360]) {
  const value = engine.apply({palette_enabled: true, palette_hue: hue,
    palette_saturation: 80, background_brightness: 6,
    accent_color: engine.hsl(hue, 80, 60)});
  assert.equal(value.palette_hue, hue);
  assert.equal(properties.get('--background'), engine.resolve(value).palette['--background']);
  assert.ok(contrast(properties.get('--text'), properties.get('--panel')) >= 4.5);
}
const custom = engine.apply({background_color: '#123456', panel_color: '#234567',
  sidebar_color: '#112233', field_color: '#223344', text_color: '#ffffff',
  muted_color: '#ddeeff', border_color: '#8899aa', border_radius: 24,
  glow_enabled: false, gradients: false, borders: false});
assert.equal(properties.get('--background'), '#123456');
assert.equal(properties.get('--panel'), '#234567');
assert.equal(properties.get('--text'), '#ffffff');
assert.equal(properties.get('--line'), '#8899aa');
assert.equal(properties.get('--radius'), '24px');
assert.equal(properties.get('--surface-border'), 'transparent');
assert.equal(properties.get('--glow-blur'), '0.0px');
assert.equal(properties.get('--page-gradient'), 'none');
assert.equal(properties.get('--accent-end'), custom.accent_color);
engine.apply({...custom, panel_color: null});
assert.notEqual(properties.get('--panel'), '#234567');
assert.equal(engine.resolve({background_color: '#ffffff', text_color: '#ffffff'}).low_contrast, true);
assert.equal(engine.normalize({border_radius: true, palette_hue: 500}).border_radius, 16);
assert.equal(engine.normalize({background_color: 'url(https://example.test)'}).background_color, null);
console.log('Custom backgrounds, text, automatic colors, palette spectrum, effects and contrast warnings OK.');
