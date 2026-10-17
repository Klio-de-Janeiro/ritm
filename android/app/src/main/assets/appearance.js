"use strict";
let appearanceDraft=null,appearanceDirty=false;

function phoneAppearance(settings) {
  if(Object.hasOwn(settings,"theme")) return RitmTheme.normalize(settings);
  // Старые настройки телефона сохраняются до первого явного изменения темы.
  const hue=settings.hue??190;
  return RitmTheme.normalize({palette_enabled:true,palette_hue:hue,
    background_brightness:settings.brightness??6,
    background_color:settings.background??null,
    accent_color:settings.accent||RitmTheme.hsl(hue,90,72)});
}

function renderAppearance() {
  if(!appearanceDirty) appearanceDraft=phoneAppearance(state.settings);
  RitmTheme.apply(appearanceDraft,{cache:!appearanceDirty});
  const form=$("#appearanceForm"),resolved=RitmTheme.resolve(appearanceDraft);
  form.querySelectorAll('input[name="theme"]').forEach(input=>input.checked=input.value===appearanceDraft.theme);
  form.querySelectorAll('[data-appearance-range]').forEach(input=>{
    if(document.activeElement!==input) input.value=appearanceDraft[input.dataset.appearanceRange];
  });
  form.querySelectorAll('[data-appearance-bool]').forEach(input=>input.checked=appearanceDraft[input.dataset.appearanceBool]);
  form.querySelectorAll('[data-appearance-color]').forEach(input=>{
    const key=input.dataset.appearanceColor;
    if(document.activeElement!==input) input.value=key==="accent_color"?appearanceDraft.accent_color:resolved.palette[RitmTheme.colors[key]]||appearanceDraft.accent_color;
  });
  form.querySelectorAll('[data-auto-color]').forEach(button=>button.disabled=button.dataset.autoColor!=="accent_color"&&!appearanceDraft[button.dataset.autoColor]);
  for(const [id,key,unit] of [["paletteHueValue","palette_hue","°"],["paletteSaturationValue","palette_saturation","%"],
    ["backgroundBrightnessValue","background_brightness","%"],["neonValue","neon_intensity","%"],["borderRadiusValue","border_radius"," px"]]) $("#"+id).textContent=appearanceDraft[key]+unit;
  $("#neonIntensity").disabled=!appearanceDraft.glow_enabled;
  $("#contrastWarning").hidden=!resolved.low_contrast;
  $("#appearanceSave").disabled=!appearanceDirty;
  $("#appearanceCancel").hidden=!appearanceDirty;
  $("#appearanceStatus").textContent=appearanceDirty?"Предпросмотр. Сохрани тему, чтобы оставить изменения.":"Оформление сохранено.";
}

function previewAppearance(changes) {
  appearanceDraft=RitmTheme.normalize({...appearanceDraft,...changes});
  appearanceDirty=JSON.stringify(appearanceDraft)!==JSON.stringify(phoneAppearance(state.settings));
  renderAppearance();
}

function paletteChange(changes) {
  const draft={...appearanceDraft,...changes};
  previewAppearance({...Object.fromEntries(Object.keys(RitmTheme.colors).map(key=>[key,null])),...changes,
    palette_enabled:true,accent_color:RitmTheme.hsl(draft.palette_hue,draft.palette_saturation,60)});
}

function setupAppearance() {
  $("#appearanceForm").addEventListener("input",event=>{
    const input=event.target,key=input.dataset.appearanceRange;
    if(input.name==="theme") {
      const [h,s,l,accent]={neon:[190,80,6,"#35dcf7"],midnight:[255,45,7,"#a08bff"],light:[215,65,95,"#3478d4"]}[input.value];
      previewAppearance({...Object.fromEntries(Object.keys(RitmTheme.colors).map(key=>[key,null])),theme:input.value,
        palette_enabled:false,palette_hue:h,palette_saturation:s,background_brightness:l,accent_color:accent});
    } else if(key) {
      const changes={[key]:Number(input.value)};
      if(["palette_hue","palette_saturation","background_brightness"].includes(key)) paletteChange(changes);
      else previewAppearance(changes);
    } else if(input.dataset.appearanceColor) previewAppearance({[input.dataset.appearanceColor]:input.value});
    else if(input.dataset.appearanceBool) previewAppearance({[input.dataset.appearanceBool]:input.checked});
  });
  $$('[data-palette]').forEach(button=>button.onclick=()=>{
    const [hue,saturation]={ocean:[195,85],violet:[265,80],forest:[150,65],sunset:[25,90]}[button.dataset.palette];
    paletteChange({palette_hue:hue,palette_saturation:saturation});
  });
  $$('[data-auto-color]').forEach(button=>button.onclick=()=>previewAppearance({[button.dataset.autoColor]:button.dataset.autoColor==="accent_color"
    ?RitmTheme.hsl(appearanceDraft.palette_hue,appearanceDraft.palette_saturation,60):null}));
  $("#appearanceForm").onsubmit=event=>{
    event.preventDefault();protect(()=>{
      call("settings",{values:appearanceDraft});appearanceDirty=false;refresh(true);toast("Тема сохранена на телефоне.");
    });
  };
  $("#appearanceCancel").onclick=()=>{appearanceDirty=false;renderAppearance();};
  $("#appearanceReset").onclick=()=>previewAppearance({...RitmTheme.defaults});
}
