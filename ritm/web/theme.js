"use strict";

(() => {
  const STORAGE_KEY = "ritm-appearance-v1";
  const colors = Object.freeze({
    background_color: "--background", panel_color: "--panel",
    sidebar_color: "--sidebar", field_color: "--field", text_color: "--text",
    muted_color: "--muted", border_color: "--line", glow_color: "--glow-color",
  });
  const ranges = Object.freeze({
    neon_intensity: [0, 100], palette_hue: [0, 360],
    palette_saturation: [0, 100], background_brightness: [0, 100],
    border_radius: [0, 24],
  });
  const defaults = Object.freeze({
    theme: "neon", accent_color: "#35dcf7", neon_intensity: 65,
    palette_enabled: false, palette_hue: 190, palette_saturation: 80,
    background_brightness: 6, glow_enabled: true, gradients: true,
    borders: true, border_radius: 16,
    ...Object.fromEntries(Object.keys(colors).map(key => [key, null])),
  });
  const palettes = {
    neon: {
      "--background": "#080f20", "--sidebar": "#0a1428",
      "--panel": "#101d34", "--panel-hover": "#16253f",
      "--field": "#0b172c", "--line": "#2a3b57",
      "--text": "#edf3ff", "--muted": "#a1b2cf",
      "--quiet": "#8a9fbe", "--subtle": "#1b2d47",
      "--completed": "#75dfbd", "--warning": "#f3bc84",
      "--danger": "#f4a9bd", "--overlay": "#030b1bc9",
      "--shadow": "#00000035", "--scheme": "dark",
    },
    midnight: {
      "--background": "#101116", "--sidebar": "#14151d",
      "--panel": "#1b1c27", "--panel-hover": "#252735",
      "--field": "#151620", "--line": "#383b4f",
      "--text": "#f2f0ff", "--muted": "#b4b1cb",
      "--quiet": "#a19db8", "--subtle": "#2b2d3e",
      "--completed": "#8edeb7", "--warning": "#efc296",
      "--danger": "#f1b0c4", "--overlay": "#08080ed1",
      "--shadow": "#00000045", "--scheme": "dark",
    },
    light: {
      "--background": "#f2f5fb", "--sidebar": "#eaf0f8",
      "--panel": "#ffffff", "--panel-hover": "#eef3fa",
      "--field": "#f5f7fc", "--line": "#c4cee0",
      "--text": "#182542", "--muted": "#485b79",
      "--quiet": "#546887", "--subtle": "#e2eaf6",
      "--completed": "#167354", "--warning": "#92501a",
      "--danger": "#a12a4c", "--overlay": "#14294e70",
      "--shadow": "#324f7920", "--scheme": "light",
    },
  };

  /** Принимать только HEX; null означает автоматический цвет гаммы. */
  function valid_color(value) {
    return typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value);
  }

  /** Нормализовать настройки старых профилей и повреждённого кэша. */
  function normalize(values = {}) {
    if (!values || typeof values !== "object") values = {};
    const result = {...defaults};
    if (typeof values.theme === "string" && Object.hasOwn(palettes, values.theme)) result.theme = values.theme;
    if (valid_color(values.accent_color)) result.accent_color = values.accent_color.toLowerCase();
    for (const [key, [min, max]] of Object.entries(ranges)) {
      if (Number.isInteger(values[key]) && values[key] >= min && values[key] <= max) result[key] = values[key];
    }
    for (const key of ["palette_enabled", "glow_enabled", "gradients", "borders"]) {
      if (typeof values[key] === "boolean") result[key] = values[key];
    }
    for (const key of Object.keys(colors)) {
      result[key] = valid_color(values[key]) ? values[key].toLowerCase() : null;
    }
    return result;
  }

  /** Преобразовать видимый спектр HSL в HEX для всей цветовой гаммы. */
  function hsl(hue, saturation, lightness) {
    const h = ((hue % 360) + 360) % 360 / 30;
    const s = Math.max(0, Math.min(100, saturation)) / 100;
    const l = Math.max(0, Math.min(100, lightness)) / 100;
    const a = s * Math.min(l, 1 - l);
    const channel = n => {
      const k = (n + h) % 12;
      return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))))
        .toString(16).padStart(2, "0");
    };
    return "#" + channel(0) + channel(8) + channel(4);
  }

  /** Преобразовать проверенный HEX в три цветовых канала. */
  function rgb(hex) {
    return [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16));
  }

  /** Смешать два HEX-цвета для контрастного текста и градиентов. */
  function mix(first, second, fraction) {
    const a = rgb(first), b = rgb(second);
    return "#" + a.map((value, index) => Math.round(value + (b[index] - value) * fraction)
      .toString(16).padStart(2, "0")).join("");
  }

  /** Вычислить относительную яркость sRGB для проверки читаемости. */
  function luminance(color) {
    const channels = rgb(color).map(value => {
      const channel = value / 255;
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  }

  /** Сравнить яркость текста и фона. */
  function contrast(first, second) {
    const a = luminance(first), b = luminance(second);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  }

  /** Найти читаемый оттенок либо лучший контраст на разных поверхностях. */
  function readable(color, surfaces) {
    let best = color, score = Math.min(...surfaces.map(surface => contrast(color, surface)));
    if (score >= 4.5) return best;
    for (let step = 1; step <= 100; step++) {
      for (const target of ["#ffffff", "#000000"]) {
        const candidate = mix(color, target, step / 100);
        const value = Math.min(...surfaces.map(surface => contrast(candidate, surface)));
        if (value >= 4.5) return candidate;
        if (value > score) { best = candidate; score = value; }
      }
    }
    return best;
  }

  /** Построить гамму, затем применить явно выбранные пользователем цвета. */
  function resolve(values) {
    const appearance = normalize(values);
    const palette = {...palettes[appearance.theme]};
    if (appearance.palette_enabled) {
      const h = appearance.palette_hue, s = appearance.palette_saturation;
      const l = appearance.background_brightness, dark = l < 55;
      const shade = offset => hsl(h, s * 0.35, Math.max(0, Math.min(100, l + (dark ? offset : -offset))));
      Object.assign(palette, {
        "--background": shade(0), "--sidebar": shade(2), "--panel": shade(5),
        "--panel-hover": shade(9), "--field": shade(3), "--line": shade(22),
        "--subtle": shade(12), "--text": dark ? "#edf3ff" : "#182542",
        "--muted": dark ? "#a1b2cf" : "#485b79",
      });
    }
    for (const [key, variable] of Object.entries(colors)) {
      if (appearance[key]) palette[variable] = appearance[key];
    }
    const surfaces = ["--background", "--panel", "--sidebar", "--field"].map(key => palette[key]);
    if (!appearance.text_color) palette["--text"] = readable(palette["--text"], surfaces);
    if (!appearance.muted_color) palette["--muted"] = readable(palette["--muted"], surfaces);
    const dark = luminance(palette["--background"]) < 0.35;
    palette["--scheme"] = dark ? "dark" : "light";
    palette["--panel-hover"] = mix(palette["--panel"], palette["--text"], 0.05);
    palette["--subtle"] = mix(palette["--panel"], palette["--text"], 0.08);
    palette["--quiet"] = palette["--muted"];
    for (const key of ["--completed", "--warning", "--danger"]) {
      palette[key] = readable(palette[key], [palette["--panel"], palette["--field"]]);
    }
    palette["--accent"] = appearance.accent_color;
    palette["--accent-text"] = readable(appearance.accent_color, surfaces);
    return {appearance, palette, low_contrast: surfaces.some(surface =>
      contrast(palette["--text"], surface) < 4.5 || contrast(palette["--muted"], surface) < 4.5)};
  }

  /** Применить оформление; предпросмотр не меняет сохранённый кэш. */
  function apply(values, {cache = true} = {}) {
    const {appearance, palette} = resolve(values);
    const root = document.documentElement;
    root.dataset.theme = appearance.theme;
    root.dataset.gradients = String(appearance.gradients);
    root.dataset.themeDark = String(palette["--scheme"] === "dark");
    for (const [name, value] of Object.entries(palette)) root.style.setProperty(name, value);
    const accent = appearance.accent_color;
    const foreground = contrast(accent, "#000000") >= contrast(accent, "#ffffff") ? "#000000" : "#ffffff";
    const glow = appearance.glow_enabled ? appearance.neon_intensity / 100 : 0;
    const channels = rgb(accent).join(",");
    const glow_channels = rgb(appearance.glow_color || accent).join(",");
    const variables = {
      "--cyan": palette["--accent-text"], "--accent-foreground": foreground,
      "--accent-end": appearance.gradients ? mix(accent, foreground === "#ffffff" ? "#000000" : "#ffffff", 0.08) : accent,
      "--accent-soft": `rgba(${channels},0.12)`,
      "--accent-border": palette["--accent-text"] + "90",
      "--glow": `rgba(${glow_channels},${(glow * 0.42).toFixed(3)})`,
      "--glow-soft": `rgba(${glow_channels},${(glow * 0.10).toFixed(3)})`,
      "--glow-blur": `${(glow * 24).toFixed(1)}px`,
      "--glow-small": `${(glow * 8).toFixed(1)}px`,
      "--surface-border": appearance.borders ? palette["--line"] : "transparent",
      "--radius": appearance.border_radius + "px",
      "--page-gradient": appearance.gradients ? `radial-gradient(ellipse at 76% -12%,rgba(${channels},0.07),transparent 49%)` : "none",
    };
    for (const [name, value] of Object.entries(variables)) root.style.setProperty(name, value);
    root.style.colorScheme = palette["--scheme"];
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", palette["--background"]);
    if (cache) {
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(appearance)); } catch {}
    }
    return appearance;
  }

  /** Восстановить последнее сохранённое оформление до первого отображения. */
  function restore() {
    let saved = defaults;
    try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY)) || defaults; } catch {}
    return apply(saved, {cache: false});
  }

  window.RitmTheme = Object.freeze({defaults, colors, normalize, resolve, hsl, apply, restore});
  restore();
})();
