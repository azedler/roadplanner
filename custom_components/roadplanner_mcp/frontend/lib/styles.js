export const PANEL_STYLES = `<style>
      :host {
        display: block;
        height: 100%;
        min-height: 100%;
        overflow: hidden;
        color: var(--primary-text-color, #212121);
        background: var(--primary-background-color, #f5f5f5);
        font-family: var(--paper-font-body1_-_font-family, system-ui, sans-serif);
      }
      * { box-sizing: border-box; }
      button, input, select, textarea { font: inherit; }
      button { -webkit-tap-highlight-color: transparent; }
      a { color: var(--primary-color); }
      .app { height: 100%; display: grid; grid-template-rows: auto auto 1fr; overflow: hidden; position: relative; }
      .app.busy { cursor: progress; }
      .topbar { min-height: 64px; padding: max(10px, env(safe-area-inset-top)) 18px 10px; display: flex; align-items: center; justify-content: space-between; gap: 16px; background: var(--app-header-background-color, var(--primary-background-color)); border-bottom: 1px solid var(--divider-color); z-index: 4; }
      .topbar-start, .topbar-actions, .title-line { display: flex; align-items: center; gap: 12px; min-width: 0; }
      .title-group { min-width: 0; }
      .title-line { gap: 8px; }
      .title-group h1 { margin: 0; font-size: 20px; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .subtitle { color: var(--secondary-text-color); font-size: 12px; margin-top: 3px; }
      .app-icon { width: 40px; height: 40px; border-radius: 13px; display: grid; place-items: center; background: color-mix(in srgb, var(--primary-color) 16%, transparent); color: var(--primary-color); flex: 0 0 auto; }
      .app-icon ha-icon { --mdc-icon-size: 25px; }
      .icon-button { border: 0; background: transparent; color: var(--primary-text-color); width: 44px; height: 44px; border-radius: 14px; display: grid; place-items: center; cursor: pointer; }
      .icon-button:hover { background: var(--secondary-background-color); }
      .menu-button { display: none; }
      .view-badge, .status-badge, .state-pill, .count-badge, .sequence-badge { display: inline-flex; align-items: center; justify-content: center; border-radius: 999px; font-weight: 700; }
      .view-badge { padding: 4px 8px; font-size: 11px; color: var(--warning-color, #f57c00); background: color-mix(in srgb, var(--warning-color, #f57c00) 14%, transparent); }
      .trip-select { display: flex; align-items: center; gap: 8px; min-width: 220px; padding: 7px 10px; border: 1px solid var(--divider-color); border-radius: 12px; background: var(--card-background-color); }
      .trip-select ha-icon { color: var(--primary-color); }
      .trip-select select { width: 100%; min-width: 0; border: 0; outline: 0; background: transparent; color: var(--primary-text-color); }
      .navigation-shell { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: stretch; background: var(--card-background-color); border-bottom: 1px solid var(--divider-color); z-index: 3; min-width: 0; }
      .tabs { display: flex; align-items: stretch; overflow-x: auto; scrollbar-width: none; padding: 0 16px; background: transparent; border-bottom: 0; z-index: 3; min-width: 0; }
      .tabs::-webkit-scrollbar { display: none; }
      .tab { flex: 1 1 0; min-width: 112px; min-height: 58px; border: 0; border-bottom: 3px solid transparent; background: transparent; color: var(--secondary-text-color); padding: 0 14px; display: flex; align-items: center; justify-content: center; gap: 8px; font-weight: 700; cursor: pointer; white-space: nowrap; }
      .tool-tabs { position: relative; align-self: center; margin-right: 12px; }
      .tool-tabs > summary { list-style: none; min-height: 44px; padding: 0 12px; border-radius: 12px; display: inline-flex; align-items: center; gap: 7px; color: var(--secondary-text-color); cursor: pointer; font-weight: 700; }
      .tool-tabs > summary::-webkit-details-marker { display: none; }
      .tool-tabs > summary:hover, .tool-tabs[open] > summary { background: var(--secondary-background-color); color: var(--primary-color); }
      .tool-tab-grid { position: absolute; right: 0; top: calc(100% + 8px); width: min(380px, calc(100vw - 24px)); padding: 10px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; border: 1px solid var(--divider-color); border-radius: 16px; background: var(--card-background-color); box-shadow: 0 12px 36px rgba(0,0,0,.22); z-index: 30; }
      .tool-tab { min-width: 0; min-height: 52px; padding: 10px; border: 1px solid var(--divider-color); border-radius: 12px; background: var(--primary-background-color); color: var(--primary-text-color); display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 8px; text-align: left; cursor: pointer; }
      .tool-tab.active { border-color: var(--primary-color); color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 9%, var(--card-background-color)); }
      .tool-tab span:not(.count-badge) { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .tab.active { color: var(--primary-color); border-bottom-color: var(--primary-color); }
      .tab ha-icon { --mdc-icon-size: 21px; }
      .count-badge { min-width: 22px; height: 22px; padding: 0 6px; font-size: 11px; color: white; background: var(--error-color, #d32f2f); }
      .count-badge.warning { background: var(--warning-color, #f57c00); }
      .content { overflow: auto; overscroll-behavior: contain; padding: 24px max(18px, calc((100vw - 1320px) / 2)); padding-bottom: max(36px, calc(24px + env(safe-area-inset-bottom))); }
      .hero-card, .panel-card, .toolbar-card, .map-card, .route-flow-card, .handoff-card, .trip-card, .stop-card, .total-day-card { background: var(--card-background-color); border: 1px solid var(--divider-color); box-shadow: var(--ha-card-box-shadow, none); border-radius: 22px; }
      .hero-card { overflow: hidden; display: grid; grid-template-columns: 1fr; min-height: 220px; margin-bottom: 18px; }
      .hero-card.with-image { grid-template-columns: minmax(260px, 42%) 1fr; }
      .hero-image { min-height: 260px; }
      .hero-copy { padding: clamp(22px, 4vw, 44px); display: flex; flex-direction: column; justify-content: center; align-items: flex-start; }
      .hero-copy h2 { margin: 6px 0 10px; font-size: clamp(28px, 5vw, 48px); line-height: 1.04; }
      .hero-copy p { margin: 0 0 18px; color: var(--secondary-text-color); max-width: 70ch; line-height: 1.55; }
      .hero-meta { display: flex; flex-wrap: wrap; gap: 10px 18px; margin-bottom: 20px; color: var(--secondary-text-color); }
      .planning-progress { width: min(520px, 100%); height: 9px; overflow: hidden; border-radius: 999px; background: color-mix(in srgb, var(--primary-color) 12%, var(--secondary-background-color)); }
      .planning-progress > span { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--primary-color), color-mix(in srgb, var(--primary-color) 55%, #7cb342)); transition: width .35s ease; }
      .readiness-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
      .readiness-grid button { min-width: 0; min-height: 78px; padding: 12px; border: 1px solid var(--divider-color); border-radius: 14px; background: var(--primary-background-color); color: var(--primary-text-color); display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: 10px; text-align: left; cursor: pointer; }
      .readiness-grid button:hover { border-color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 6%, var(--card-background-color)); }
      .readiness-grid ha-icon { color: var(--primary-color); --mdc-icon-size: 26px; }
      .readiness-grid span { display: grid; gap: 2px; min-width: 0; }
      .readiness-grid strong { font-size: 20px; }
      .overview-technical > summary { list-style: none; display: flex; align-items: center; justify-content: space-between; gap: 12px; cursor: pointer; }
      .overview-technical > summary::-webkit-details-marker { display: none; }
      .overview-technical > summary > span { display: inline-flex; align-items: center; gap: 8px; font-weight: 800; }
      .overview-technical > summary small { color: var(--secondary-text-color); }
      .hero-meta span { display: flex; align-items: center; gap: 7px; }
      .eyebrow { display: block; color: var(--primary-color); font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
      .stat-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-bottom: 18px; }
      .stat-card { min-height: 130px; padding: 20px; border-radius: 20px; background: var(--card-background-color); border: 1px solid var(--divider-color); display: flex; flex-direction: column; justify-content: center; }
      .stat-card ha-icon { color: var(--primary-color); margin-bottom: 12px; }
      .stat-card strong { font-size: 26px; }
      .stat-card span { color: var(--secondary-text-color); margin-top: 3px; }
      .panel-card, .toolbar-card, .route-flow-card, .handoff-card { padding: 22px; margin-bottom: 18px; }
      .toolbar-card { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
      .toolbar-card h2, .panel-card h2, .section-heading h2 { margin: 4px 0 0; font-size: 23px; }
      .toolbar-card p { margin: 7px 0 0; color: var(--secondary-text-color); }
      .toolbar-actions, .button-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
      /* One flat row let unrelated controls wrap into each other - the video
         length ended up sitting next to "Letztes PDF" (live report: "Bisschen
         mehr Ordnung wär da schön"). Related controls now stay together. */
      .toolbar-actions.grouped { flex-direction: column; align-items: stretch; gap: 16px; }
      .action-group { display: flex; flex-direction: column; gap: 8px; }
      .action-group-label { font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--secondary-text-color); }
      .action-group-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
      .section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
      .section-heading.compact { margin-bottom: 14px; }
      .section-heading > ha-icon { color: var(--primary-color); --mdc-icon-size: 34px; }
      .primary-button, .secondary-button, .danger-button, .text-button { text-decoration: none; min-height: 42px; border-radius: 13px; padding: 9px 15px; border: 0; display: inline-flex; align-items: center; justify-content: center; gap: 8px; font-weight: 700; cursor: pointer; }
      .primary-button { background: var(--primary-color); color: var(--text-primary-color, white); }
      .secondary-button { background: var(--secondary-background-color); color: var(--primary-text-color); border: 1px solid var(--divider-color); }
      .danger-button { background: var(--error-color, #d32f2f); color: white; }
      .text-button { background: transparent; color: var(--primary-color); }
      .danger-text { color: var(--error-color, #d32f2f); }
      .compact-button { min-height: 36px; padding: 7px 10px; margin-left: auto; }
      button:disabled { opacity: .45; cursor: not-allowed; }
      .next-day-grid, .facts-grid, .preview-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
      .next-day-grid > div, .facts-grid > div, .preview-grid > div { padding: 14px; border-radius: 14px; background: var(--secondary-background-color); display: flex; flex-direction: column; gap: 5px; }
      .next-day-grid span, .facts-grid span, .preview-grid span { color: var(--secondary-text-color); font-size: 12px; }
      .settings-list { display: grid; gap: 0; margin-bottom: 16px; }
      .setting-row { min-height: 50px; display: flex; justify-content: space-between; align-items: center; gap: 12px; border-bottom: 1px solid var(--divider-color); }
      .setting-row:last-child { border-bottom: 0; }
      .state-pill { padding: 5px 9px; font-size: 11px; }
      .state-pill.on { color: var(--success-color, #2e7d32); background: color-mix(in srgb, var(--success-color, #2e7d32) 13%, transparent); }
      .state-pill.off { color: var(--secondary-text-color); background: var(--secondary-background-color); }
      .status-dot { width: 12px; height: 12px; border-radius: 50%; background: var(--disabled-color); }
      .status-dot.success { background: var(--success-color, #2e7d32); box-shadow: 0 0 0 5px color-mix(in srgb, var(--success-color, #2e7d32) 14%, transparent); }
      .notice { border-radius: 16px; padding: 14px 16px; margin: 12px 0; display: flex; align-items: center; gap: 12px; }
      .notice > div { display: flex; flex-direction: column; gap: 3px; }
      .notice span { color: var(--secondary-text-color); }
      .notice.info { background: color-mix(in srgb, var(--info-color, #0288d1) 12%, transparent); }
      .notice.warning { background: color-mix(in srgb, var(--warning-color, #f57c00) 13%, transparent); }
      .notice.danger { background: color-mix(in srgb, var(--error-color, #d32f2f) 12%, transparent); }
      .notice.success { background: color-mix(in srgb, var(--success-color, #2e7d32) 12%, transparent); }
      .crew-photo-picker summary { display: flex; align-items: center; gap: 8px; cursor: pointer; font-weight: 600; }
      .crew-photo-current { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 14px; margin: 10px 0; padding: 10px; border-radius: 12px; background: color-mix(in srgb, var(--primary-color) 10%, transparent); }
      /* The image sizes itself (no object-fit letterboxing), so the frame
         rectangle maps 1:1 onto the photo - otherwise the crop box would
         sit next to the pixels it claims to select. touch-action: none
         lets the box be dragged without scrolling the dialog. */
      .crew-crop-frame { position: relative; flex: 0 1 300px; max-width: 300px; line-height: 0; cursor: crosshair; touch-action: none; user-select: none; }
      .crew-crop-frame img { width: 100%; height: auto; border-radius: 10px; display: block; pointer-events: none; }
      .crew-crop-box { position: absolute; border: 2px solid var(--primary-color); border-radius: 6px; box-shadow: 0 0 0 9999px rgba(0,0,0,.42); pointer-events: none; }
      .crew-crop-controls { flex: 1 1 200px; display: flex; flex-direction: column; gap: 8px; }
      .crew-crop-size { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--secondary-text-color); }
      .crew-crop-size input { width: 100%; }
      .crew-photo-pager { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 10px; font-size: 13px; color: var(--secondary-text-color); }
      .crew-photo-pager button[disabled] { opacity: .4; }
      /* Fixed tile height instead of aspect-ratio: Safari/WebView ignores
         aspect-ratio on <button>, which made the grid collapse into ragged
         columns of full-height photos (live report). */
      .crew-photo-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(78px, 1fr)); gap: 8px; margin-top: 10px; max-height: 320px; overflow-y: auto; padding: 2px; }
      .crew-photo-choice { position: relative; display: block; width: 100%; height: 82px; padding: 0; border: 2px solid var(--divider-color); border-radius: 10px; overflow: hidden; background: var(--secondary-background-color); cursor: pointer; }
      .crew-photo-choice img { width: 100%; height: 100%; object-fit: cover; display: block; }
      .crew-photo-choice.selected { border-color: var(--primary-color); box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary-color) 45%, transparent); }
      .crew-photo-choice.selected::after { content: "✓"; position: absolute; right: 4px; bottom: 3px; width: 20px; height: 20px; border-radius: 50%; background: var(--primary-color); color: #fff; font: 700 13px/20px system-ui, sans-serif; text-align: center; }
      .trip-video-status .text-button { margin-left: 4px; }
      .view-notice { margin-top: 0; }
      .day-cover-hero { padding: 0; overflow: hidden; display: grid; grid-template-columns: minmax(280px, 42%) minmax(0, 1fr); min-height: 240px; }
      .day-cover-image { min-height: 240px; }
      .day-cover-image .destination-image { min-height: 240px; }
      .day-cover-copy { padding: clamp(20px, 4vw, 38px); display: flex; flex-direction: column; justify-content: center; }
      .day-cover-copy h2 { margin: 6px 0 10px; font-size: clamp(25px, 4vw, 38px); }
      .day-cover-copy p { margin: 0; color: var(--secondary-text-color); line-height: 1.55; }
      .route-layout { display: grid; grid-template-columns: minmax(0, 2fr) minmax(280px, .8fr); gap: 18px; align-items: start; }
      .route-main { min-width: 0; }
      .day-facts { position: sticky; top: 0; }
      .day-toolbar .day-select { min-width: 250px; }
      .day-select { display: flex; flex-direction: column; gap: 5px; color: var(--secondary-text-color); font-size: 12px; }
      .day-select select { min-height: 44px; border: 1px solid var(--divider-color); border-radius: 12px; padding: 0 12px; background: var(--card-background-color); color: var(--primary-text-color); }
      .map-card { overflow: hidden; margin-bottom: 18px; }
      .map-stage { height: clamp(300px, 52vh, 560px); position: relative; background: var(--secondary-background-color); }
      .map-stage ha-map { display: block; width: 100%; height: 100%; opacity: 0; transition: opacity .2s ease; }
      .map-overlay { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; color: var(--secondary-text-color); pointer-events: none; }
      .map-ready .map-stage ha-map { opacity: 1; }
      .map-ready .map-overlay { display: none; }
      .map-failed .map-overlay span { display: none; }
      .map-failed .map-overlay::after { content: "Kartenkomponente nicht verfügbar"; }
      .map-key { display: flex; gap: 8px; overflow-x: auto; padding: 10px 14px; border-top: 1px solid var(--divider-color); scrollbar-width: thin; }
      .map-key-item, .map-key-more { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 7px; min-height: 30px; padding: 4px 10px 4px 5px; border-radius: 999px; background: var(--secondary-background-color); color: var(--secondary-text-color); font-size: 11px; }
      .map-key-item b { width: 22px; height: 22px; display: grid; place-items: center; border-radius: 50%; background: var(--primary-color); color: white; font-size: 10px; }
      .map-key-item.inherited b { background: var(--secondary-text-color); }
      .map-key-item.missing { border: 1px dashed var(--warning-color, #d89b16); background: color-mix(in srgb, var(--warning-color, #d89b16) 10%, var(--card-background-color)); }
      .map-key-item.missing b { background: transparent; color: var(--warning-color, #b87900); border: 1px dashed currentColor; }
      .location-status { display: flex; align-items: flex-start; gap: 10px; padding: 10px 12px; border-radius: 12px; margin: 10px 0; }
      .location-status ha-icon { flex: 0 0 auto; margin-top: 1px; }
      .location-status div { min-width: 0; display: grid; gap: 2px; }
      .location-status span { color: var(--secondary-text-color); font-size: 12px; line-height: 1.35; }
      .location-status.warning { background: color-mix(in srgb, var(--warning-color, #d89b16) 13%, var(--card-background-color)); color: var(--warning-color, #b87900); }
      .location-status.neutral { background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .map-caption { padding: 10px 14px; display: flex; gap: 8px; align-items: center; color: var(--secondary-text-color); font-size: 12px; border-top: 1px solid var(--divider-color); }
      .map-unavailable { padding: 0; }
      .map-placeholder { min-height: 230px; padding: 30px; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 8px; color: var(--secondary-text-color); }
      .map-placeholder ha-icon { --mdc-icon-size: 46px; color: var(--primary-color); }
      .route-flow-card { overflow-x: auto; }
      .route-flow { display: flex; align-items: stretch; min-width: max-content; padding: 18px 8px 8px; }
      .flow-item { width: 180px; position: relative; display: grid; grid-template-columns: 44px 1fr; gap: 10px; align-items: start; }
      .flow-node { width: 42px; height: 42px; border-radius: 50%; display: grid; place-items: center; background: var(--primary-color); color: white; position: relative; z-index: 2; font-weight: 800; }
      .flow-item.inherited .flow-node { background: var(--secondary-text-color); }
      .flow-item.legacy { opacity: .72; }
      .flow-copy { display: flex; flex-direction: column; gap: 3px; padding-top: 2px; }
      .flow-copy strong { max-width: 120px; }
      .flow-copy span { color: var(--secondary-text-color); font-size: 12px; }
      .flow-line { position: absolute; height: 4px; width: 136px; left: 42px; top: 19px; background: color-mix(in srgb, var(--primary-color) 50%, var(--divider-color)); }
      .notes-block { white-space: pre-wrap; line-height: 1.5; color: var(--secondary-text-color); }
      .image-section { overflow: hidden; }
      .image-gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
      .gallery-item { margin: 0; min-width: 0; border: 1px solid var(--divider-color); border-radius: 16px; overflow: hidden; background: var(--secondary-background-color); }
      .gallery-item figcaption { padding: 11px 12px; display: flex; flex-direction: column; gap: 4px; }
      .gallery-item figcaption span { color: var(--secondary-text-color); font-size: 11px; }
      .destination-image { height: 100%; min-height: 220px; position: relative; overflow: hidden; background: var(--secondary-background-color); }
      .destination-image.compact { min-height: 170px; height: 190px; }
      .destination-image img { width: 100%; height: 100%; object-fit: cover; display: block; }
      .image-fallback { display: none; position: absolute; inset: 0; align-items: center; justify-content: center; flex-direction: column; gap: 7px; color: var(--secondary-text-color); }
      .destination-image.image-error img { display: none; }
      .destination-image.image-error .image-fallback { display: flex; }
      .destination-gallery-preview { height: 190px; display: grid; grid-template-columns: minmax(0, 2fr) minmax(74px, .8fr); gap: 4px; overflow: hidden; background: var(--secondary-background-color); }
      .destination-gallery-preview.compact { height: 190px; }
      .destination-gallery-main, .destination-gallery-thumbs button { position: relative; min-width: 0; border: 0; padding: 0; background: var(--secondary-background-color); cursor: pointer; overflow: hidden; }
      .destination-gallery-main img, .destination-gallery-thumbs img { width: 100%; height: 100%; object-fit: cover; display: block; }
      .destination-gallery-main > span { position: absolute; left: 10px; bottom: 10px; display: inline-flex; align-items: center; gap: 5px; padding: 5px 8px; border-radius: 999px; background: rgba(0,0,0,.62); color: white; font-size: 11px; font-weight: 800; }
      .destination-gallery-main ha-icon { --mdc-icon-size: 16px; }
      .destination-gallery-thumbs { display: grid; grid-template-rows: repeat(2, minmax(0, 1fr)); gap: 4px; min-width: 0; }
      .destination-gallery-inline { margin-top: 12px; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 11px; border-radius: 13px; background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .destination-gallery-inline.warning { color: var(--warning-color, #f57c00); background: color-mix(in srgb, var(--warning-color, #f57c00) 9%, var(--secondary-background-color)); }
      .destination-gallery-inline > div { min-width: 0; display: grid; gap: 2px; }
      .destination-gallery-inline strong { color: var(--primary-text-color); }
      .destination-gallery-inline span { font-size: 11px; line-height: 1.35; }
      .destination-gallery-inline ha-icon { --mdc-icon-size: 22px; }
      .empty-inline { min-height: 120px; display: flex; align-items: center; justify-content: center; gap: 16px; color: var(--secondary-text-color); text-align: left; }
      .empty-inline ha-icon { --mdc-icon-size: 42px; color: var(--primary-color); }
      .empty-inline > div { display: flex; flex-direction: column; gap: 4px; }
      .stop-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(310px, 1fr)); gap: 16px; }
      .stop-card { overflow: hidden; min-width: 0; }
      .stop-image-placeholder, .trip-card-placeholder { height: 190px; display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 8px; background: linear-gradient(135deg, color-mix(in srgb, var(--primary-color) 14%, var(--secondary-background-color)), var(--secondary-background-color)); color: var(--primary-color); }
      .stop-image-placeholder ha-icon, .trip-card-placeholder ha-icon { --mdc-icon-size: 48px; }
      .stop-card-body { padding: 18px; }
      .stop-card-heading { display: flex; gap: 10px; align-items: flex-start; }
      .stop-card-heading h3 { margin: 0 0 3px; }
      .stop-card-heading span:not(.sequence-badge) { color: var(--secondary-text-color); font-size: 12px; }
      .sequence-badge { width: 28px; height: 28px; background: var(--primary-color); color: white; flex: 0 0 auto; }
      .stop-meta { display: grid; gap: 6px; margin: 14px 0; color: var(--secondary-text-color); font-size: 12px; }
      .stop-meta span { display: flex; align-items: center; gap: 7px; }
      .stop-card-body p { white-space: pre-wrap; line-height: 1.5; }
      .attribution { color: var(--secondary-text-color); font-size: 11px; margin: 8px 0; }
      .stop-actions { margin-top: 14px; }
      .stop-order-body { padding: 0 22px 22px; display: grid; gap: 14px; }
      .stop-order-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
      .stop-order-row { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 10px 12px; align-items: center; padding: 14px; border: 1px solid var(--divider-color); border-radius: 14px; background: var(--secondary-background-color); }
      .stop-order-copy { min-width: 0; display: grid; gap: 3px; }
      .stop-order-copy strong { overflow-wrap: anywhere; }
      .stop-order-copy span { color: var(--secondary-text-color); font-size: 12px; }
      .stop-order-controls { grid-column: 1 / -1; display: flex; align-items: end; justify-content: flex-end; gap: 10px; flex-wrap: wrap; }
      .stop-order-position { display: grid; gap: 4px; color: var(--secondary-text-color); font-size: 11px; }
      .stop-order-position select { min-width: 82px; min-height: 44px; border: 1px solid var(--divider-color); border-radius: 10px; padding: 0 10px; background: var(--card-background-color); color: var(--primary-text-color); }
      .stop-order-buttons { display: flex; gap: 8px; }
      .stop-order-buttons .icon-button { width: 44px; height: 44px; }
      .stop-order-buttons .icon-button:disabled { opacity: .35; cursor: default; }
      .trip-route-graphic { overflow: hidden; }
      .journey-track { display: flex; align-items: stretch; overflow-x: auto; padding: 8px 2px 14px; scrollbar-width: thin; }
      .journey-node { flex: 0 0 230px; min-height: 122px; border: 1px solid var(--divider-color); border-radius: 17px; padding: 14px; background: var(--secondary-background-color); color: var(--primary-text-color); display: grid; grid-template-columns: 38px 1fr; gap: 11px; text-align: left; cursor: pointer; }
      .journey-node:hover, .journey-node:focus-visible { border-color: var(--primary-color); outline: none; }
      .journey-dot { width: 36px; height: 36px; border-radius: 50%; display: grid; place-items: center; background: var(--primary-color); color: white; font-weight: 800; }
      .journey-copy { min-width: 0; display: flex; flex-direction: column; gap: 5px; }
      .journey-copy small, .journey-copy span { color: var(--secondary-text-color); }
      .journey-copy strong { font-size: 15px; line-height: 1.25; }
      .journey-copy span { font-size: 12px; line-height: 1.35; }
      .journey-line { flex: 0 0 48px; height: 4px; margin-top: 31px; background: color-mix(in srgb, var(--primary-color) 55%, var(--divider-color)); }
      .total-route-list { margin-top: 22px; }
      .total-day-card { margin-bottom: 12px; padding: 12px; display: grid; grid-template-columns: 52px 120px 1fr auto; gap: 14px; align-items: center; cursor: pointer; }
      .total-day-card:hover { border-color: var(--primary-color); }
      .total-day-sequence { width: 46px; height: 46px; border-radius: 50%; display: grid; place-items: center; background: color-mix(in srgb, var(--primary-color) 14%, transparent); color: var(--primary-color); font-size: 18px; font-weight: 800; }
      .total-day-image .destination-image { min-height: 80px; height: 80px; border-radius: 12px; }
      .total-day-copy > span, .total-day-copy p { color: var(--secondary-text-color); }
      .total-day-copy h3 { margin: 3px 0; }
      .total-day-copy p { margin: 0 0 8px; }
      .total-day-copy > div { display: flex; flex-wrap: wrap; gap: 7px; }
      .total-day-copy > div span { padding: 4px 7px; border-radius: 8px; background: var(--secondary-background-color); font-size: 11px; }
      .chevron { color: var(--secondary-text-color); }
      .crew-section { background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 22px; padding: 22px; margin-bottom: 18px; }
      .crew-section h3 { margin: 0 0 12px; }
      .crew-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
      .crew-row { display: flex; align-items: center; gap: 14px; padding: 10px 14px; border: 1px solid var(--divider-color); border-radius: 14px; }
      .crew-row.inactive { opacity: 0.6; }
      .crew-row ha-icon { --mdc-icon-size: 28px; color: var(--primary-color); }
      .system-check { margin-top: 12px; display: flex; flex-direction: column; gap: 10px; }
      .system-check-summary { display: flex; flex-wrap: wrap; gap: 6px; }
      .system-check-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
      .system-check-row { display: flex; align-items: flex-start; gap: 10px; padding: 8px 10px; border: 1px solid var(--divider-color); border-radius: 12px; }
      .system-check-row > div { display: flex; flex-direction: column; min-width: 0; }
      .system-check-row span { color: var(--secondary-text-color); font-size: 13px; overflow-wrap: anywhere; }
      .system-check-row .system-check-hint { color: var(--primary-color); }
      .system-check-row.ok ha-icon { color: var(--success-color, #2e7d32); }
      .system-check-row.warn ha-icon { color: var(--warning-color, #ed6c02); }
      .system-check-row.fail ha-icon { color: var(--error-color, #c62828); }
      .system-check-row.skipped ha-icon { color: var(--disabled-text-color); }
      .crew-row-avatar { flex: 0 0 auto; width: 44px; height: 44px; border-radius: 50%; overflow: hidden; background: var(--secondary-background-color); }
      .crew-row-avatar img { width: 100%; height: 100%; object-fit: cover; display: block; }
      /* A cropped avatar positions the image itself, so "cover" must not
         pre-crop it to a centred square first - that is what made the
         shown region differ from the picked one. */
      .crew-row-avatar.cropped { position: relative; }
      .crew-row-avatar.cropped img { position: absolute; left: 0; top: 0; object-fit: fill; max-width: none; }
      .crew-row-body { flex: 1; display: flex; flex-direction: column; }
      .crew-row-body span { color: var(--secondary-text-color); font-size: 13px; }
      .crew-retired { margin-top: 14px; }
      .crew-retired summary { cursor: pointer; color: var(--secondary-text-color); margin-bottom: 10px; }
      .pitch-preferences > summary { list-style: none; display: inline-flex; align-items: center; gap: 8px; cursor: pointer; font-weight: 700; }
      .pitch-preferences > summary::-webkit-details-marker { display: none; }
      .pitch-preferences[open] > summary { margin-bottom: 12px; color: var(--primary-color); }
      .pitch-day-card .pitch-active-row { margin-bottom: 10px; }
      .pitch-day-card .pitch-active-row span { display: inline-flex; align-items: center; gap: 7px; }
      .pitch-option-list .secondary-button, .pitch-option-list .text-button { min-height: 36px; padding: 0 10px; }
      .pitch-options-heading { display: flex; align-items: center; gap: 8px; margin: 18px 0 8px; padding-top: 14px; border-top: 1px solid var(--divider-color); font-size: 17px; }
      .pitch-options-heading ha-icon { color: var(--primary-color); }
      .pitch-route-dot { display: inline-block; width: 11px; height: 11px; border-radius: 50%; margin-right: 6px; vertical-align: baseline; border: 1.5px solid rgba(255,255,255,.75); box-shadow: 0 0 0 1px rgba(0,0,0,.15); }
      .pitch-plan-b .setting-row span { display: inline-flex; align-items: center; gap: 7px; }
      .pitch-route-flow { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 4px 0 14px; color: var(--secondary-text-color); font-size: 14px; }
      .pitch-route-flow span { display: inline-flex; align-items: center; gap: 5px; }
      .pitch-route-flow ha-icon[icon="mdi:arrow-right-thin"] { color: var(--secondary-text-color); }
      .pitch-day-card .map-card { margin-bottom: 14px; }
      .pitch-chip-row { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0; }
      .pitch-chip { display: inline-flex; align-items: center; gap: 3px; padding: 2px 9px; border-radius: 999px; font-size: 12px; font-weight: 600; }
      .pitch-chip ha-icon { --mdc-icon-size: 14px; }
      .pitch-chip-pro { background: rgba(76, 175, 80, 0.16); color: #4caf50; }
      .pitch-chip-con { background: rgba(244, 67, 54, 0.14); color: #f44336; }
      .pitch-chip-route { background: rgba(33, 150, 243, 0.14); color: var(--primary-color); }
      .pitch-route-summary span { display: block; }
      .pitch-option-cover { width: 56px; height: 56px; border-radius: 12px; object-fit: cover; flex-shrink: 0; }
      .pitch-plan-b-cover { width: 100%; max-height: 160px; border-radius: 14px; object-fit: cover; margin-bottom: 10px; }
      .crew-checkbox-group { display: flex; flex-direction: column; gap: 6px; }
      .trip-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 18px; }
      .assistant-basket-quickbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; padding: 12px 16px; margin: 12px 0; border-left: 4px solid var(--primary-color); }
      .assistant-basket-quickbar .basket-quick-label { display: inline-flex; align-items: center; gap: 8px; }
      .trip-card { overflow: hidden; }
      .trip-card.active { border-color: color-mix(in srgb, var(--success-color, #2e7d32) 65%, var(--divider-color)); }
      .trip-card.selected { box-shadow: 0 0 0 2px var(--primary-color); }
      .trip-card-body { padding: 18px; }
      .trip-title-row { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
      .trip-title-row h3 { margin: 4px 0 0; }
      .trip-card-body > p { color: var(--secondary-text-color); }
      .trip-stats { display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 16px; }
      .trip-stats span { padding: 5px 8px; border-radius: 9px; background: var(--secondary-background-color); font-size: 12px; }
      .status-badge { padding: 5px 9px; font-size: 11px; }
      .status-badge.success { color: var(--success-color, #2e7d32); background: color-mix(in srgb, var(--success-color, #2e7d32) 13%, transparent); }
      .status-badge.warning { color: var(--warning-color, #f57c00); background: color-mix(in srgb, var(--warning-color, #f57c00) 13%, transparent); }
      .status-badge.danger { color: var(--error-color, #d32f2f); background: color-mix(in srgb, var(--error-color, #d32f2f) 12%, transparent); }
      .status-badge.neutral { color: var(--secondary-text-color); background: var(--secondary-background-color); }
      .handoff-list { display: grid; gap: 14px; }
      .handoff-heading { display: flex; justify-content: space-between; gap: 16px; }
      .handoff-heading h3 { margin: 4px 0 6px; }
      .handoff-heading p { margin: 0; color: var(--secondary-text-color); }
      .handoff-meta { display: flex; flex-wrap: wrap; gap: 9px 16px; margin: 15px 0; color: var(--secondary-text-color); font-size: 12px; }
      .handoff-meta span { display: flex; align-items: center; gap: 6px; }
      .operation-summary { padding: 11px; border-radius: 12px; background: var(--secondary-background-color); margin-bottom: 12px; }
      .loading-state, .empty-state { min-height: 360px; display: flex; align-items: center; justify-content: center; flex-direction: column; text-align: center; gap: 9px; color: var(--secondary-text-color); }
      .empty-state ha-icon { --mdc-icon-size: 56px; color: var(--primary-color); }
      .empty-state h2 { margin: 4px 0 0; color: var(--primary-text-color); }
      .empty-state p { max-width: 55ch; margin: 0 0 8px; }
      .compact-empty { min-height: 230px; border: 1px dashed var(--divider-color); border-radius: 20px; padding: 20px; }
      .spinner { width: 38px; height: 38px; border: 4px solid color-mix(in srgb, var(--primary-color) 20%, transparent); border-top-color: var(--primary-color); border-radius: 50%; animation: spin .8s linear infinite; }
      .spinner.small { width: 28px; height: 28px; border-width: 3px; }
      @keyframes spin { to { transform: rotate(360deg); } }
      .progress { position: absolute; top: 0; left: 0; right: 0; height: 3px; z-index: 20; overflow: hidden; background: color-mix(in srgb, var(--primary-color) 20%, transparent); }
      .progress::after { content: ""; display: block; width: 35%; height: 100%; background: var(--primary-color); animation: progress 1s ease-in-out infinite; }
      @keyframes progress { from { transform: translateX(-120%); } to { transform: translateX(390%); } }
      .toast-host { position: fixed; right: 22px; top: max(18px, env(safe-area-inset-top)); z-index: 1000; pointer-events: none; }
      .toast { max-width: min(420px, calc(100vw - 32px)); padding: 13px 16px; border-radius: 15px; color: white; display: flex; align-items: center; gap: 10px; box-shadow: 0 8px 30px rgba(0,0,0,.22); pointer-events: auto; }
      .toast.success { background: var(--success-color, #2e7d32); }
      .toast.error { background: var(--error-color, #d32f2f); }
      .place-enrichment-body { display: grid; gap: 16px; padding: 8px 22px 4px; }
      .place-enrichment-toolbar { display: grid; gap: 10px; }
      .place-enrichment-toolbar > .secondary-button, .place-enrichment-toolbar > .status-pill { justify-self: start; }
      .place-enrichment-item { display: grid; gap: 12px; padding: 16px; border: 1px solid var(--divider-color); border-radius: 18px; background: var(--card-background-color); }
      .place-enrichment-item > header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
      .place-enrichment-item h3 { margin: 3px 0 2px; }
      .place-enrichment-item p { margin: 0; color: var(--secondary-text-color); font-size: 12px; }
      .place-candidates { display: grid; gap: 12px; }
      .place-candidate { overflow: hidden; border: 1px solid var(--divider-color); border-radius: 16px; background: var(--secondary-background-color); }
      .place-candidate.selected { border-color: var(--primary-color); box-shadow: 0 0 0 2px color-mix(in srgb, var(--primary-color) 18%, transparent); }
      .place-candidate-select { width: 100%; display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 10px; align-items: start; padding: 13px; border: 0; background: transparent; color: inherit; text-align: left; cursor: pointer; }
      .place-candidate-select span:last-child { min-width: 0; display: grid; gap: 3px; }
      .place-candidate-select small { color: var(--secondary-text-color); line-height: 1.35; }
      .place-radio ha-icon { color: var(--primary-color); --mdc-icon-size: 23px; }
      .google-provider-attribution { display: grid; gap: 3px; margin: 0 13px 12px; padding: 10px 12px; border-radius: 12px; background: var(--card-background-color); border: 1px solid var(--divider-color); }
      .google-provider-attribution .google-maps-label { color: var(--primary-text-color); font-family: Roboto, Sans-Serif; font-size: 13px; font-style: normal; font-weight: 400; letter-spacing: normal; line-height: normal; white-space: nowrap; }
      .google-provider-attribution span:not(.google-maps-label) { color: var(--secondary-text-color); font-size: 11px; line-height: 1.4; }
      .provider-ranking-info { padding: 0 13px 4px; color: var(--secondary-text-color); font-size: 12px; }
      .provider-ranking-info summary { cursor: pointer; color: var(--primary-text-color); font-weight: 600; }
      .provider-ranking-info p { margin: 8px 0 0; line-height: 1.5; }
      .place-image-strip { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 3px; height: 132px; background: var(--divider-color); }
      .place-image-strip img { width: 100%; height: 100%; object-fit: cover; display: block; }
      .place-image-empty, .place-no-match { display: flex; align-items: center; gap: 10px; padding: 14px; color: var(--secondary-text-color); background: var(--secondary-background-color); }
      .place-image-empty ha-icon, .place-no-match ha-icon { --mdc-icon-size: 25px; }
      .place-candidate-details { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; padding: 12px 13px 4px; }
      .place-candidate-details > div { min-width: 0; display: grid; gap: 2px; }
      .place-candidate-details span { color: var(--secondary-text-color); font-size: 11px; }
      .place-candidate-details strong, .place-candidate-details a { overflow-wrap: anywhere; font-size: 12px; }
      .place-chips { display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 13px; }
      .place-chips span { padding: 5px 8px; border-radius: 999px; background: color-mix(in srgb, var(--primary-color) 9%, var(--card-background-color)); font-size: 11px; }
      .compact-row { padding: 8px 13px 13px; }
      .place-cleanup-suggestion { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px; border: 1px dashed var(--divider-color); border-radius: 14px; background: color-mix(in srgb, var(--primary-color) 5%, var(--card-background-color)); }
      .place-cleanup-suggestion.selected { border-color: var(--primary-color); }
      .place-p4n-suggestion { border-style: solid; border-color: color-mix(in srgb, var(--primary-color) 45%, var(--divider-color)); }
      .place-cleanup-suggestion > div, .place-manual-heading > div { min-width: 0; display: grid; gap: 3px; }
      .place-cleanup-suggestion small, .place-manual-heading small { color: var(--secondary-text-color); line-height: 1.4; }
      .place-manual-form { display: grid; gap: 12px; padding: 13px; border: 1px solid var(--divider-color); border-radius: 16px; background: var(--secondary-background-color); }
      .place-manual-form.selected { border-color: var(--primary-color); box-shadow: 0 0 0 2px color-mix(in srgb, var(--primary-color) 14%, transparent); }
      .place-manual-heading { display: flex; justify-content: space-between; align-items: start; gap: 12px; }
      .compact-form-grid { padding: 0; gap: 10px; }
      .place-manual-form > .secondary-button { justify-self: start; }
      .place-enrichment-actions { position: sticky; bottom: 0; background: var(--card-background-color); z-index: 2; }

      .modal-backdrop { position: absolute; inset: 0; z-index: 25; background: rgba(0,0,0,.55); display: flex; align-items: center; justify-content: center; padding: 24px; }
      .modal { width: min(760px, 100%); max-height: min(880px, calc(100% - 20px)); overflow: auto; border-radius: 24px; background: var(--card-background-color); color: var(--primary-text-color); box-shadow: 0 24px 70px rgba(0,0,0,.35); }
      .modal-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 22px 22px 12px; position: sticky; top: 0; background: var(--card-background-color); z-index: 2; }
      .modal-header h2 { margin: 0; }
      .modal-header p { margin: 5px 0 0; color: var(--secondary-text-color); }
      .action-error-body { padding: 18px 22px 8px; display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 14px; align-items: start; }
      .action-error-icon { width: 48px; height: 48px; border-radius: 16px; display: grid; place-items: center; color: white; background: var(--error-color, #d32f2f); }
      .action-error-icon ha-icon { --mdc-icon-size: 29px; }
      .action-error-body p { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.55; }
      .action-error-request { margin-top: 14px; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; color: var(--secondary-text-color); }
      .action-error-request code { user-select: all; max-width: 100%; overflow-wrap: anywhere; padding: 5px 8px; border-radius: 8px; background: var(--secondary-background-color); color: var(--primary-text-color); }
      .action-error-details { margin-top: 12px; }
      .action-error-details summary { cursor: pointer; color: var(--secondary-text-color); }
      .action-error-details code { display: block; margin-top: 8px; padding: 10px; border-radius: 10px; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--secondary-background-color); }
      .action-error-actions { flex-wrap: wrap; }
      .action-error-actions .primary-button { margin-left: auto; }
      .form-grid { padding: 10px 22px 22px; display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
      .form-field { display: flex; flex-direction: column; gap: 6px; color: var(--secondary-text-color); font-size: 12px; }
      .form-field.full, .form-section.full, .modal-actions.full { grid-column: 1 / -1; }
      .form-field input, .form-field select, .form-field textarea { width: 100%; min-height: 45px; border-radius: 12px; border: 1px solid var(--divider-color); background: var(--primary-background-color); color: var(--primary-text-color); padding: 10px 12px; outline: 0; }
      .form-field textarea { resize: vertical; }
      .form-field input:focus, .form-field select:focus, .form-field textarea:focus { border-color: var(--primary-color); box-shadow: 0 0 0 2px color-mix(in srgb, var(--primary-color) 20%, transparent); }
      .form-section { padding-top: 8px; border-top: 1px solid var(--divider-color); }
      .form-section h3 { margin: 0 0 3px; }
      .form-section p { margin: 0; color: var(--secondary-text-color); }
      .modal-actions { padding: 16px 22px max(22px, env(safe-area-inset-bottom)); display: flex; justify-content: flex-end; gap: 10px; }
      .confirm-body { padding: 18px 26px; display: flex; align-items: center; gap: 16px; }
      .confirm-body ha-icon { --mdc-icon-size: 42px; color: var(--warning-color, #f57c00); }
      .preview-body, .image-search-body { padding: 8px 22px 20px; }
      .preview-status { padding: 15px; border-radius: 16px; display: flex; gap: 12px; align-items: center; margin-bottom: 16px; }
      .preview-status.ready { background: color-mix(in srgb, var(--success-color, #2e7d32) 12%, transparent); }
      .preview-status.blocked { background: color-mix(in srgb, var(--warning-color, #f57c00) 12%, transparent); }
      .preview-status > div { display: flex; flex-direction: column; gap: 3px; }
      .operation-list { padding-left: 22px; }
      .operation-list pre { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--secondary-background-color); padding: 10px; border-radius: 10px; }
      .image-search-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
      .image-result { border: 1px solid var(--divider-color); border-radius: 16px; overflow: hidden; background: var(--secondary-background-color); }
      .image-result > div:last-child { padding: 12px; display: flex; flex-direction: column; gap: 8px; }
      .image-result > div:last-child span { color: var(--secondary-text-color); font-size: 11px; }
      .image-result.selected { border-color: var(--primary-color); box-shadow: 0 0 0 2px color-mix(in srgb, var(--primary-color) 18%, transparent); }
      .image-result > div:last-child small { color: var(--secondary-text-color); font-size: 10px; }
      .image-result-actions { display: flex; flex-wrap: wrap; gap: 7px; }
      .destination-provider-errors { margin-top: 14px; padding: 12px; border-radius: 14px; background: color-mix(in srgb, var(--warning-color, #f57c00) 10%, var(--secondary-background-color)); display: grid; gap: 5px; color: var(--secondary-text-color); }
      .destination-provider-errors strong { color: var(--primary-text-color); }
      .destination-provider-errors span { font-size: 11px; overflow-wrap: anywhere; }
      .assistant-setup { display: grid; grid-template-columns: auto 1fr; gap: 20px; align-items: start; }
      .assistant-setup-icon, .assistant-avatar { width: 58px; height: 58px; border-radius: 18px; display: grid; place-items: center; background: color-mix(in srgb, var(--primary-color) 14%, transparent); color: var(--primary-color); }
      .assistant-setup-icon ha-icon, .assistant-avatar ha-icon { --mdc-icon-size: 34px; }
      .assistant-setup h2 { margin: 5px 0 8px; }
      .assistant-setup p { color: var(--secondary-text-color); line-height: 1.55; }
      .assistant-toolbar { display: flex; justify-content: space-between; gap: 20px; align-items: center; }
      .assistant-toolbar h2 { margin: 4px 0 6px; }
      .assistant-toolbar p { margin: 0; color: var(--secondary-text-color); max-width: 76ch; }
      .assistant-toolbar-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
      .assistant-model { min-height: 40px; padding: 8px 12px; border-radius: 12px; display: inline-flex; align-items: center; gap: 7px; color: var(--secondary-text-color); background: var(--secondary-background-color); font-size: 12px; }
      .assistant-health { min-height: 40px; padding: 8px 12px; border-radius: 12px; display: inline-flex; align-items: center; gap: 7px; font-size: 12px; font-weight: 700; background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .assistant-health.success { color: var(--success-color, #2e7d32); background: color-mix(in srgb, var(--success-color, #2e7d32) 11%, transparent); }
      .assistant-health.warning { color: var(--warning-color, #f57c00); background: color-mix(in srgb, var(--warning-color, #f57c00) 11%, transparent); }
      .assistant-status-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
      .assistant-status-card { margin-bottom: 0; min-width: 0; display: flex; align-items: flex-start; gap: 12px; padding: 15px 16px; }
      .assistant-status-card > ha-icon { --mdc-icon-size: 25px; color: var(--primary-color); margin-top: 2px; }
      .assistant-status-card > div { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
      .assistant-status-card span { color: var(--secondary-text-color); font-size: 10px; text-transform: uppercase; letter-spacing: .06em; }
      .assistant-status-card strong { overflow-wrap: anywhere; }
      .assistant-status-card small { color: var(--secondary-text-color); line-height: 1.35; overflow-wrap: anywhere; }
      .assistant-retry-notice { align-items: center; }
      .assistant-retry-notice button { margin-left: auto; flex: 0 0 auto; }
      .assistant-layout { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(300px, .7fr); gap: 18px; align-items: stretch; }
      .assistant-chat, .assistant-basket { margin-bottom: 0; min-width: 0; }
      .assistant-chat { padding: 0; overflow: hidden; display: grid; grid-template-rows: auto minmax(360px, 1fr); min-height: min(720px, calc(100vh - 250px)); }
      .assistant-thread { overflow: auto; overscroll-behavior: contain; padding: 22px; display: flex; flex-direction: column; gap: 16px; scroll-behavior: smooth; }
      .assistant-welcome { margin: auto; max-width: 720px; text-align: center; padding: 24px 0; }
      .assistant-welcome .assistant-avatar { margin: 0 auto 14px; }
      .assistant-welcome h3 { margin: 0 0 8px; font-size: 25px; }
      .assistant-welcome > p { color: var(--secondary-text-color); margin: 0 auto 22px; max-width: 62ch; line-height: 1.55; }
      .quick-prompt-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
      .quick-prompt-grid button { min-height: 110px; padding: 14px; border: 1px solid var(--divider-color); border-radius: 16px; background: var(--secondary-background-color); color: var(--primary-text-color); display: flex; flex-direction: column; align-items: flex-start; justify-content: space-between; gap: 12px; text-align: left; cursor: pointer; }
      .quick-prompt-grid button:hover { border-color: var(--primary-color); }
      .quick-prompt-grid ha-icon { color: var(--primary-color); }
      .assistant-message { display: flex; gap: 11px; max-width: min(860px, 92%); }
      .assistant-message.user { align-self: flex-end; flex-direction: row-reverse; }
      .assistant-message.assistant { align-self: flex-start; }
      .assistant-message.status { opacity: .9; }
      .message-avatar { width: 34px; height: 34px; border-radius: 50%; display: grid; place-items: center; flex: 0 0 auto; background: var(--secondary-background-color); color: var(--primary-color); }
      .message-avatar ha-icon { --mdc-icon-size: 20px; }
      .assistant-message.user .message-avatar { background: color-mix(in srgb, var(--primary-color) 18%, transparent); }
      .message-body { min-width: 0; padding: 13px 15px; border-radius: 18px; background: var(--secondary-background-color); border: 1px solid var(--divider-color); }
      .assistant-message.user .message-body { background: color-mix(in srgb, var(--primary-color) 12%, var(--card-background-color)); }
      .assistant-message.status .message-body { border-style: dashed; }
      .message-meta { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 7px; font-size: 11px; }
      .message-meta span { color: var(--secondary-text-color); }
      .message-text { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.55; }
      .message-text .assistant-inline-link { display: inline-flex; max-width: 100%; align-items: center; gap: 4px; color: var(--primary-color); font-weight: 700; text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 2px; vertical-align: baseline; overflow-wrap: anywhere; word-break: break-word; }
      .message-text .assistant-inline-link ha-icon { --mdc-icon-size: 15px; flex: 0 0 auto; }
      .message-text .assistant-inline-link span { min-width: 0; overflow-wrap: anywhere; }
      .message-text .assistant-inline-link.google-maps { padding: 2px 6px; border-radius: 8px; background: color-mix(in srgb, var(--primary-color) 10%, transparent); text-decoration: none; }
      .message-text .assistant-inline-link:hover { filter: brightness(1.08); }
      .message-text .assistant-inline-link:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; border-radius: 6px; }
      .assistant-pending-group { display: grid; gap: 10px; }
      .assistant-message.pending { opacity: .92; }
      .assistant-message.pending.thinking .message-body { border-style: dashed; }
      .assistant-thinking { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; color: var(--secondary-text-color); }
      .assistant-thinking > span { width: 7px; height: 7px; border-radius: 999px; background: var(--primary-color); animation: roadplanner-thinking 1.15s infinite ease-in-out; }
      .assistant-thinking > span:nth-child(2) { animation-delay: .16s; }
      .assistant-thinking > span:nth-child(3) { animation-delay: .32s; }
      .assistant-thinking strong { margin-left: 3px; font-size: 12px; font-weight: 700; }
      @keyframes roadplanner-thinking { 0%, 80%, 100% { opacity: .28; transform: translateY(0); } 40% { opacity: 1; transform: translateY(-3px); } }
      .message-basket-status { margin-top: 11px; padding: 9px 10px; border-radius: 11px; display: flex; align-items: flex-start; gap: 7px; font-size: 11px; line-height: 1.4; border: 1px solid var(--divider-color); background: var(--card-background-color); }
      .message-basket-status ha-icon { --mdc-icon-size: 17px; flex: 0 0 auto; }
      .message-basket-status.success { color: var(--success-color, #2e7d32); border-color: color-mix(in srgb, var(--success-color, #2e7d32) 35%, var(--divider-color)); }
      .message-basket-status.warning { color: var(--warning-color, #f57c00); border-color: color-mix(in srgb, var(--warning-color, #f57c00) 35%, var(--divider-color)); }
      .message-sources { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--divider-color); display: flex; flex-wrap: wrap; gap: 7px; }
      .message-sources > span { width: 100%; color: var(--secondary-text-color); font-size: 10px; text-transform: uppercase; letter-spacing: .06em; }
      .message-sources a { max-width: 100%; padding: 6px 8px; border-radius: 9px; background: var(--card-background-color); display: inline-flex; align-items: center; gap: 5px; font-size: 11px; text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .message-sources a ha-icon { --mdc-icon-size: 15px; }
      .assistant-composer { padding: 14px; border-top: 1px solid var(--divider-color); background: var(--card-background-color); }
      .assistant-composer-top { border-top: 0; border-bottom: 1px solid var(--divider-color); }
      .assistant-composer-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin: 0 4px 7px; }
      .assistant-composer-heading label { color: var(--secondary-text-color); font-size: 11px; }
      .assistant-composer-heading span { display: inline-flex; align-items: center; gap: 5px; color: var(--secondary-text-color); font-size: 10px; }
      .assistant-composer-heading ha-icon { --mdc-icon-size: 15px; }
      .assistant-toolbar-primary { margin-bottom: 14px; }
      .assistant-main-actions .assistant-briefing-button { min-height: 44px; }
      .assistant-technical { margin-top: 18px; padding: 0; overflow: hidden; }
      .assistant-technical > summary { list-style: none; cursor: pointer; padding: 15px 18px; display: flex; justify-content: space-between; align-items: center; gap: 14px; }
      .assistant-technical > summary::-webkit-details-marker { display: none; }
      .assistant-technical > summary span { display: inline-flex; align-items: center; gap: 8px; font-weight: 800; }
      .assistant-technical > summary small { color: var(--secondary-text-color); }
      .assistant-technical[open] > summary { border-bottom: 1px solid var(--divider-color); }
      .assistant-technical-content { padding: 16px; display: grid; gap: 14px; }
      .assistant-technical-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 9px; }
      .assistant-technical .assistant-status-grid { margin: 0; }
      .assistant-technical .assistant-status-card { background: var(--secondary-background-color); border: 1px solid var(--divider-color); border-radius: 14px; }
      .assistant-composer > label { display: block; color: var(--secondary-text-color); font-size: 11px; margin: 0 0 6px 4px; }
      .assistant-input-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 9px; align-items: end; }
      .assistant-input-row textarea { width: 100%; min-height: 54px; max-height: 180px; resize: vertical; border: 1px solid var(--divider-color); border-radius: 16px; background: var(--primary-background-color); color: var(--primary-text-color); padding: 13px 14px; outline: none; }
      .assistant-input-row textarea:focus { border-color: var(--primary-color); box-shadow: 0 0 0 2px color-mix(in srgb, var(--primary-color) 18%, transparent); }
      .assistant-send { min-height: 52px; }
      .assistant-hint { margin: 8px 4px 0; display: flex; align-items: center; gap: 5px; color: var(--secondary-text-color); font-size: 11px; }
      .assistant-hint ha-icon { --mdc-icon-size: 16px; color: var(--success-color, #2e7d32); }
      .assistant-basket { position: sticky; top: 0; align-self: start; }
      .basket-counter { min-width: 38px; height: 38px; border-radius: 13px; display: grid; place-items: center; background: color-mix(in srgb, var(--primary-color) 14%, transparent); color: var(--primary-color); font-weight: 800; }
      .basket-list { display: grid; gap: 10px; margin-bottom: 16px; max-height: min(480px, 55vh); overflow: auto; }
      .basket-item { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 10px; align-items: start; padding: 12px; border: 1px solid var(--divider-color); border-radius: 15px; background: var(--secondary-background-color); }
      .basket-item-icon { width: 34px; height: 34px; border-radius: 11px; display: grid; place-items: center; background: var(--card-background-color); color: var(--primary-color); }
      .basket-item-icon ha-icon { --mdc-icon-size: 20px; }
      .basket-item-copy { min-width: 0; }
      .basket-item-copy > strong { display: block; line-height: 1.35; overflow-wrap: anywhere; }
      .basket-item-copy p { margin: 5px 0 0; color: var(--secondary-text-color); font-size: 11px; line-height: 1.4; }
      .basket-item-label { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 5px; font-size: 10px; text-transform: uppercase; letter-spacing: .04em; color: var(--secondary-text-color); }
      .basket-item-label b { color: var(--primary-color); }
      .basket-item-actions { display: flex; flex-direction: column; gap: 2px; }
      .basket-map-link { margin-top: 8px; }
      .basket-map-link .text-link { display: inline-flex; align-items: center; gap: 5px; font-size: .82rem; font-weight: 700; color: var(--primary-color); text-decoration: none; }
      .basket-item-actions .icon-button { width: 36px; height: 36px; border-radius: 11px; }
      .draft-summary-grid { margin: 0 22px 18px; }
      .basket-remove { width: 34px; height: 34px; border-radius: 10px; }
      .basket-empty { min-height: 230px; padding: 20px; border: 1px dashed var(--divider-color); border-radius: 16px; display: flex; align-items: center; justify-content: center; flex-direction: column; text-align: center; gap: 8px; color: var(--secondary-text-color); margin-bottom: 16px; }
      .basket-empty ha-icon { --mdc-icon-size: 40px; color: var(--primary-color); }
      .basket-empty strong { color: var(--primary-text-color); }
      .basket-empty span { font-size: 12px; line-height: 1.45; }
      .full-width { width: 100%; }
      .basket-footnote { margin: 10px 2px 0; color: var(--secondary-text-color); font-size: 11px; line-height: 1.45; }
      .assistant-diagnostics-body { padding: 8px 22px 6px; }
      .diagnostics-grid { margin-bottom: 18px; }
      .diagnostics-section { padding: 16px 0; border-top: 1px solid var(--divider-color); }
      .diagnostics-section h3 { margin: 0 0 8px; }
      .diagnostics-section p { margin: 0; color: var(--secondary-text-color); line-height: 1.5; }
      .diagnostics-plugin-list { display: flex; flex-wrap: wrap; gap: 8px; }
      .diagnostics-records { display: grid; gap: 9px; }
      .diagnostics-record { padding: 12px; border: 1px solid var(--divider-color); border-left-width: 4px; border-radius: 13px; background: var(--secondary-background-color); overflow: hidden; }
      .diagnostics-record.ok { border-left-color: var(--success-color, #2e7d32); }
      .diagnostics-record.error { border-left-color: var(--warning-color, #f57c00); }
      .diagnostics-record > div { display: flex; justify-content: space-between; gap: 12px; }
      .diagnostics-record > div span { color: var(--secondary-text-color); font-size: 11px; }
      .diagnostics-record p, .diagnostics-record small { display: block; margin: 5px 0 0; color: var(--secondary-text-color); overflow-wrap: anywhere; }
      .diagnostics-record small { font-family: var(--code-font-family, monospace); white-space: pre-wrap; }
      .diagnostics-record .diagnostic-error { color: var(--error-color, #d32f2f); }
      .inherited-stop { border-style: dashed; background: color-mix(in srgb, var(--primary-color) 4%, var(--card-background-color)); }
      .inherited-badge { margin: 10px 0 0 38px; padding: 7px 9px; border-radius: 10px; display: inline-flex; align-items: center; gap: 6px; color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 10%, transparent); font-size: 11px; font-weight: 700; }
      .inherited-badge ha-icon { --mdc-icon-size: 16px; }
      .assistant-input-actions { display: flex; align-items: center; gap: 8px; }
      .assistant-attach { flex: 0 0 auto; border: 1px solid var(--divider-color); background: var(--secondary-background-color); }
      .archive-toolbar { align-items: flex-start; }
      .archive-toolbar-actions { justify-content: flex-end; }
      .archive-stats { margin-top: 0; }
      .archive-summary-card { border-left: 4px solid var(--primary-color); }
      .section-count { min-width: 36px; height: 36px; display: grid; place-items: center; border-radius: 12px; background: var(--secondary-background-color); color: var(--secondary-text-color); font-weight: 800; }
      .archive-paste-zone { min-height: 128px; border: 2px dashed var(--divider-color); border-radius: 18px; display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: 14px; padding: 20px; background: var(--secondary-background-color); cursor: text; outline: none; }
      .archive-paste-zone:focus, .archive-paste-zone.drag-active { border-color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 7%, var(--secondary-background-color)); }
      .archive-paste-zone > ha-icon { color: var(--primary-color); --mdc-icon-size: 34px; }
      .archive-paste-zone strong, .archive-paste-zone span { display: block; }
      .archive-paste-zone span { margin-top: 4px; color: var(--secondary-text-color); line-height: 1.45; }
      .archive-card-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
      .archive-document-card { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 14px; padding: 16px; border: 1px solid var(--divider-color); border-radius: 18px; background: var(--secondary-background-color); min-width: 0; }
      .archive-card-icon, .archive-row-icon { width: 46px; height: 46px; display: grid; place-items: center; border-radius: 14px; color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 12%, var(--card-background-color)); }
      .archive-card-icon ha-icon { --mdc-icon-size: 27px; }
      .archive-card-main { min-width: 0; }
      .archive-card-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
      .archive-card-heading > div { min-width: 0; }
      .archive-card-heading span:not(.status-badge) { color: var(--secondary-text-color); font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }
      .archive-card-heading h3 { margin: 3px 0 0; font-size: 17px; overflow-wrap: anywhere; }
      .archive-card-main > p { margin: 10px 0; color: var(--secondary-text-color); line-height: 1.45; }
      .archive-card-meta { display: flex; flex-wrap: wrap; gap: 7px 12px; color: var(--secondary-text-color); font-size: 11px; }
      .archive-card-meta span { display: inline-flex; align-items: center; gap: 4px; }
      .archive-card-meta ha-icon { --mdc-icon-size: 15px; }
      .archive-card-actions { margin-top: 12px; }
      .archive-list { display: grid; gap: 9px; }
      .archive-row { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; gap: 12px; align-items: center; padding: 12px; border: 1px solid var(--divider-color); border-radius: 15px; background: var(--secondary-background-color); }
      .archive-row-copy { min-width: 0; }
      .archive-row-copy strong, .archive-row-copy span, .archive-row-copy small { display: block; }
      .archive-row-copy span, .archive-row-copy small { color: var(--secondary-text-color); font-size: 11px; margin-top: 3px; line-height: 1.35; }
      .archive-row-value { text-align: right; white-space: nowrap; }
      .archive-row-value strong, .archive-row-value span { display: block; }
      .archive-row-value span { color: var(--secondary-text-color); font-size: 11px; margin-top: 3px; }
      .archive-row-actions { display: flex; align-items: center; }
      .archive-row-actions .icon-button { width: 38px; height: 38px; border-radius: 11px; }
      .todo-check { width: 44px; height: 44px; display: grid; place-items: center; border: 0; background: transparent; color: var(--primary-color); cursor: pointer; }
      .archive-todo-row.done { opacity: .62; }
      .archive-todo-row.done .archive-row-copy strong { text-decoration: line-through; }
      .archive-todo-row.due-overdue { border-color: color-mix(in srgb, var(--error-color, #d32f2f) 55%, var(--divider-color)); }
      .archive-todo-row.due-today { border-color: color-mix(in srgb, var(--warning-color, #f57c00) 55%, var(--divider-color)); }
      .todo-badges { display: grid; justify-items: end; gap: 5px; }
      .due-badge, .priority-badge { padding: 5px 8px; border-radius: 999px; font-size: 10px; font-weight: 800; background: var(--secondary-background-color); white-space: nowrap; }
      .due-badge.due-overdue { color: var(--error-color, #d32f2f); background: color-mix(in srgb, var(--error-color, #d32f2f) 12%, transparent); }
      .due-badge.due-today, .due-badge.due-upcoming { color: var(--warning-color, #f57c00); background: color-mix(in srgb, var(--warning-color, #f57c00) 12%, transparent); }
      .priority-badge { background: var(--secondary-background-color); }
      .priority-high { color: var(--error-color, #d32f2f); background: color-mix(in srgb, var(--error-color, #d32f2f) 12%, transparent); }
      .priority-low { color: var(--secondary-text-color); }
      .status-badge.status-success { color: var(--success-color, #2e7d32); background: color-mix(in srgb, var(--success-color, #2e7d32) 13%, transparent); }
      .status-badge.status-warning { color: var(--warning-color, #f57c00); background: color-mix(in srgb, var(--warning-color, #f57c00) 13%, transparent); }
      .status-badge.status-info { color: var(--info-color, var(--primary-color)); background: color-mix(in srgb, var(--primary-color) 12%, transparent); }
      .warning-text { color: var(--warning-color, #f57c00); }
      .day-archive-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
      .day-archive-grid > div { padding: 14px; border-radius: 15px; background: var(--secondary-background-color); min-width: 0; }
      .archive-mini-heading { display: block; margin-bottom: 8px; color: var(--secondary-text-color); font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; }
      .archive-mini-item { width: 100%; border: 0; border-top: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: 8px; padding: 9px 0; text-align: left; cursor: pointer; }
      .archive-mini-item:first-of-type { border-top: 0; }
      .archive-mini-item ha-icon { color: var(--primary-color); --mdc-icon-size: 20px; }
      .archive-mini-item strong, .archive-mini-item small { display: block; overflow-wrap: anywhere; }
      .archive-mini-item small { color: var(--secondary-text-color); margin-top: 2px; }
      .archive-day-total { display: block; font-size: 21px; margin-bottom: 3px; }
      .stop-archive-summary { margin-top: 10px; }
      .stop-archive-counts { display: flex; flex-wrap: wrap; gap: 7px; }
      .stop-archive-counts span { display: inline-flex; align-items: center; gap: 4px; padding: 5px 8px; border-radius: 999px; background: var(--secondary-background-color); color: var(--secondary-text-color); font-size: 11px; }
      .stop-archive-counts ha-icon { --mdc-icon-size: 15px; }
      .stop-archive-actions { margin-top: 7px; }
      .stop-archive-actions .text-button { min-height: 34px; padding: 5px 8px; font-size: 11px; }
      .checkbox-field { min-height: 52px; display: flex; align-items: flex-start; gap: 10px; padding: 11px; border: 1px solid var(--divider-color); border-radius: 13px; background: var(--secondary-background-color); }
      .checkbox-field input { width: 20px; height: 20px; margin-top: 2px; accent-color: var(--primary-color); }
      .checkbox-field span, .checkbox-field strong, .checkbox-field small { display: block; }
      .checkbox-field small { margin-top: 3px; color: var(--secondary-text-color); line-height: 1.35; }
      .archive-review-form .form-section { margin-top: 6px; }
      .archive-analysis-todos { display: grid; gap: 12px; }
      .archive-analysis-todo { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; padding: 14px; border: 1px solid var(--divider-color); border-radius: 16px; background: var(--secondary-background-color); }
      .archive-analysis-todo .checkbox-field, .archive-analysis-todo .form-field.full { grid-column: 1 / -1; }
      .count-badge.info { background: color-mix(in srgb, var(--primary-color) 88%, white); }
      .message-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
      .message-actions .text-button { min-height: 34px; padding: 6px 10px; font-size: 11px; }
      .decision-intro, .media-toolbar { display: flex; justify-content: space-between; gap: 20px; align-items: center; }
      .decision-intro h2, .media-toolbar h2 { margin: 4px 0 6px; }
      .decision-list { display: grid; gap: 20px; }
      .decision-card { overflow: hidden; padding: 0; }
      .decision-heading { display: flex; justify-content: space-between; gap: 18px; padding: 20px 22px 14px; }
      .decision-heading h2 { margin: 3px 0 5px; }
      .decision-heading p { margin: 0; color: var(--secondary-text-color); }
      .decision-counter { flex: 0 0 auto; align-self: flex-start; padding: 7px 10px; border-radius: 999px; background: var(--secondary-background-color); font-size: 12px; font-weight: 800; }
      .decision-slide { display: grid; grid-template-columns: minmax(280px, .9fr) minmax(320px, 1.1fr); min-height: 420px; }
      .decision-image { position: relative; min-height: 360px; background: var(--secondary-background-color); overflow: hidden; display: grid; place-items: center; }
      .decision-image img { width: 100%; height: 100%; object-fit: cover; position: absolute; inset: 0; }
      .decision-image small { position: absolute; left: 10px; right: 10px; bottom: 10px; padding: 5px 8px; border-radius: 8px; background: rgba(0,0,0,.58); color: white; font-size: 10px; z-index: 1; }
      .decision-image.empty { color: var(--secondary-text-color); align-content: center; gap: 10px; text-align: center; padding: 24px; }
      .decision-image.empty ha-icon { --mdc-icon-size: 56px; }
      .decision-option-gallery { display: grid; grid-template-rows: minmax(0, 1fr) 92px; min-height: 420px; background: var(--secondary-background-color); }
      .decision-option-gallery .decision-image { min-height: 0; border: 0; padding: 0; cursor: pointer; }
      .decision-image-thumbs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px; padding-top: 4px; }
      .decision-image-thumbs button { border: 0; padding: 0; background: var(--secondary-background-color); cursor: pointer; overflow: hidden; }
      .decision-image-thumbs img { width: 100%; height: 100%; object-fit: cover; display: block; }
      .decision-copy { padding: 24px; display: flex; flex-direction: column; gap: 16px; }
      .decision-title-row { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
      .decision-title-badges { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }
      .decision-title-row h3 { font-size: 25px; margin: 3px 0 0; }
      .decision-copy > p { margin: 0; line-height: 1.55; }
      .decision-metrics { display: flex; flex-wrap: wrap; gap: 8px; }
      .decision-metrics span { display: inline-flex; align-items: center; gap: 6px; padding: 7px 10px; border-radius: 999px; background: var(--secondary-background-color); font-size: 12px; font-weight: 700; }
      .decision-metrics ha-icon { --mdc-icon-size: 18px; color: var(--primary-color); }
      .decision-procon { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
      .decision-procon > div { padding: 14px; border-radius: 14px; background: var(--secondary-background-color); }
      .decision-procon ul { margin: 8px 0 0; padding-left: 18px; color: var(--secondary-text-color); }
      .decision-procon li + li { margin-top: 4px; }
      .decision-actions { margin-top: auto; }
      .decision-footer { display: grid; grid-template-columns: auto 1fr auto auto; align-items: center; gap: 10px; padding: 12px 18px; border-top: 1px solid var(--divider-color); }
      .decision-dots { display: flex; justify-content: center; gap: 7px; }
      .decision-dot { width: 10px; height: 10px; border-radius: 999px; border: 0; background: var(--divider-color); padding: 0; cursor: pointer; }
      .decision-dot.active { width: 24px; background: var(--primary-color); }
      .media-toolbar-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
      .import-toolbar { align-items: flex-start; }
      .import-toolbar > div:first-child { max-width: 760px; }
      .import-stats { margin-top: 0; }
      .import-explainer { border-left: 4px solid var(--primary-color); }
      .import-flow { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
      .import-flow span { padding: 8px 11px; border-radius: 999px; background: var(--secondary-background-color); font-weight: 700; font-size: 12px; }
      .import-flow ha-icon { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
      .import-card-grid { display: grid; gap: 14px; }
      .import-card { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 14px; }
      .import-card-icon { width: 52px; height: 52px; display: grid; place-items: center; border-radius: 16px; background: color-mix(in srgb, var(--primary-color) 12%, var(--card-background-color)); color: var(--primary-color); }
      .import-card-icon ha-icon { --mdc-icon-size: 30px; }
      .import-card-copy { min-width: 0; }
      .import-card-copy p { color: var(--secondary-text-color); line-height: 1.5; }
      .import-card-title { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
      .import-card-title h3 { margin: 2px 0 0; overflow-wrap: anywhere; }
      .attachment-purpose-body, .universal-import-review-body { padding: 8px 22px 22px; display: grid; gap: 16px; }
      .attachment-summary { display: flex; align-items: center; gap: 12px; padding: 14px; border-radius: 16px; background: var(--secondary-background-color); }
      .attachment-summary ha-icon { --mdc-icon-size: 34px; color: var(--primary-color); }
      .attachment-summary span, .attachment-purpose-card small { display: block; margin-top: 4px; color: var(--secondary-text-color); }
      .attachment-purpose-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
      .attachment-purpose-card { appearance: none; border: 1px solid var(--divider-color); border-radius: 18px; padding: 18px; background: var(--secondary-background-color); color: inherit; display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 14px; text-align: left; cursor: pointer; }
      .attachment-purpose-card:hover, .attachment-purpose-card:focus-visible { border-color: var(--primary-color); outline: none; background: color-mix(in srgb, var(--primary-color) 6%, var(--secondary-background-color)); }
      .attachment-purpose-card ha-icon { --mdc-icon-size: 32px; color: var(--primary-color); }
      .import-review-section { border: 1px solid var(--divider-color); border-radius: 16px; padding: 14px; }
      .import-review-section h3 { margin: 0 0 8px; }
      .import-review-section p, .import-review-section ul { margin: 0; line-height: 1.55; color: var(--secondary-text-color); }
      .import-preview-list { display: grid; gap: 8px; max-height: 320px; overflow: auto; }
      .import-preview-item { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 10px; align-items: start; padding: 10px; border-radius: 12px; background: var(--secondary-background-color); }
      .import-preview-item ha-icon { color: var(--primary-color); }
      .import-preview-item span { display: block; margin-top: 3px; color: var(--secondary-text-color); font-size: 12px; }
      .universal-import-actions { flex-wrap: wrap; }
      .onedrive-setup-form code { font-family: var(--code-font-family, monospace); }
      .setup-steps { margin: 8px 0 14px; padding-left: 22px; display: grid; gap: 7px; color: var(--secondary-text-color); line-height: 1.45; }
      .inline-link-button { display: inline-flex; width: fit-content; text-decoration: none; }
      .media-stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
      .media-stat { display: flex; gap: 13px; align-items: center; min-height: 90px; }
      .media-stat > ha-icon { --mdc-icon-size: 32px; color: var(--primary-color); }
      .media-stat strong, .media-stat span { display: block; }
      .media-stat strong { font-size: 24px; }
      .media-stat span { color: var(--secondary-text-color); font-size: 12px; }
      .media-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
      .media-controls { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; }
      .media-filter-row { display: flex; flex-wrap: wrap; gap: 6px; }
      .media-filter-chip { border: 1px solid var(--divider-color); border-radius: 999px; padding: 4px 12px; }
      .media-filter-chip.active { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
      .media-page-row { display: flex; align-items: center; gap: 10px; }
      .media-page-row span { color: var(--secondary-text-color); font-size: 0.9em; }
      .media-page-row button[disabled] { opacity: 0.4; pointer-events: none; }
      .media-card { border: 1px solid var(--divider-color); border-radius: 18px; background: var(--card-background-color); overflow: hidden; display: grid; grid-template-rows: 190px auto auto; box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,.12)); }
      .media-card.cover { border-color: color-mix(in srgb, var(--primary-color) 60%, var(--divider-color)); }
      .media-thumb { position: relative; width: 100%; height: 190px; border: 0; padding: 0; background: var(--secondary-background-color); cursor: pointer; overflow: hidden; }
      .media-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
      .cover-badge { position: absolute; top: 9px; left: 9px; display: inline-flex; align-items: center; gap: 4px; padding: 5px 8px; border-radius: 999px; background: rgba(0,0,0,.68); color: white; font-size: 10px; font-weight: 800; }
      .cover-badge ha-icon { --mdc-icon-size: 15px; }
      .media-card-copy { padding: 12px 14px 7px; min-width: 0; }
      .media-card-title { display: flex; justify-content: space-between; gap: 8px; align-items: flex-start; }
      .media-card-title strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .media-card-copy > span, .media-card-copy > small { display: block; color: var(--secondary-text-color); margin-top: 5px; font-size: 11px; }
      .media-card-actions { display: flex; justify-content: flex-end; gap: 4px; padding: 4px 8px 10px; }
      .media-card-actions .icon-button { width: 38px; height: 38px; }
      .onedrive-auth-body { display: grid; justify-items: center; gap: 15px; padding: 26px 24px; text-align: center; }
      .onedrive-auth-body > ha-icon { --mdc-icon-size: 58px; color: #0078d4; }
      .device-code { font-size: 31px; letter-spacing: .12em; font-weight: 900; padding: 14px 18px; border-radius: 14px; background: var(--secondary-background-color); user-select: all; }
      .experience-album { display: grid; gap: 10px; }
      .experience-album-actions { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
      .experience-album-heading { display: flex; justify-content: space-between; align-items: end; gap: 12px; }
      .experience-album-heading > div { display: grid; gap: 2px; }
      .experience-album-heading small { color: var(--secondary-text-color); }
      .experience-album-strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(92px, 1fr)); gap: 8px; }
      .experience-album.compact { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--divider-color); }
      .experience-album.compact .experience-album-strip { grid-template-columns: repeat(5, minmax(0, 1fr)); }
      .experience-album-thumb { position: relative; border: 0; padding: 0; min-height: 78px; border-radius: 10px; overflow: hidden; background: var(--secondary-background-color); cursor: pointer; }
      .experience-album-thumb img { width: 100%; height: 100%; min-height: 78px; max-height: 130px; object-fit: cover; display: block; }
      .experience-album-thumb ha-icon { position: absolute; top: 5px; right: 5px; color: #fff; filter: drop-shadow(0 1px 3px #000); }
      .stop-experience-cover { position: relative; width: 100%; min-height: 180px; border: 0; padding: 0; overflow: hidden; background: var(--secondary-background-color); cursor: pointer; }
      .stop-experience-cover img { width: 100%; height: 100%; min-height: 180px; max-height: 260px; object-fit: cover; display: block; }
      .stop-experience-cover span { position: absolute; left: 10px; bottom: 10px; display: inline-flex; align-items: center; gap: 6px; padding: 6px 9px; border-radius: 999px; background: rgba(0,0,0,.68); color: #fff; font-size: 12px; font-weight: 700; }
      .day-experience-album { margin-top: 16px; }
      .media-gallery { padding: 0 18px 14px; }
      .media-gallery-stage { height: min(62vh, 680px); min-height: 320px; display: grid; place-items: center; background: #111; border-radius: 16px; overflow: hidden; }
      .media-gallery-stage img { max-width: 100%; max-height: 100%; object-fit: contain; }
      .media-gallery-caption { display: grid; gap: 4px; padding: 12px 4px 0; }
      .media-gallery-caption span, .media-gallery-caption small { color: var(--secondary-text-color); }
      .media-gallery-actions { justify-content: center; }
      .onedrive-sync-notice > div { min-width: 0; display: grid; gap: 5px; }
      .onedrive-sync-notice span, .onedrive-sync-notice small { line-height: 1.45; overflow-wrap: anywhere; word-break: break-word; }
      .onedrive-current-folder { display: -webkit-box; max-width: 100%; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow-wrap: anywhere; word-break: break-word; }
      .onedrive-current-folder b { font-weight: 800; }
      .muted { color: var(--secondary-text-color); }

      /* 2.6.3: keep the panel inside the real HA/webview width. */
      :host { width: 100%; max-width: 100%; min-width: 0; container-type: inline-size; }
      .app, .content { width: 100%; max-width: 100%; min-width: 0; }
      .content { overflow-x: hidden; }
      .content > * { min-width: 0; max-width: 100%; }
      .topbar { width: 100%; max-width: 100%; min-width: 0; overflow: hidden; }
      .topbar-start { flex: 1 1 0; min-width: 0; overflow: hidden; }
      .topbar-actions { flex: 0 1 auto; min-width: 0; max-width: 48%; overflow: hidden; }
      .trip-select { max-width: 100%; min-width: 0; }
      .assistant-layout,
      .assistant-chat,
      .assistant-thread,
      .assistant-composer,
      .assistant-input-row { width: 100%; max-width: 100%; min-width: 0; }
      .assistant-thread { overflow-x: hidden; }
      .assistant-message { min-width: 0; }
      .message-body { flex: 1 1 auto; width: auto; max-width: 100%; min-width: 0; overflow: hidden; }
      .message-meta { min-width: 0; flex-wrap: wrap; }
      .message-text, .message-basket-status, .message-sources { min-width: 0; max-width: 100%; }
      .message-text { word-break: break-word; }
      .message-sources a { min-width: 0; }
      .assistant-input-row textarea { min-width: 0; max-width: 100%; }

      @container (max-width: 720px) {
        .assistant-layout { grid-template-columns: minmax(0, 1fr); }
        .assistant-basket { position: static; }
        .assistant-message { width: 100%; max-width: 100%; }
        .assistant-message .message-body { flex: 1 1 0%; max-width: calc(100% - 45px); }
        .assistant-thread { padding-left: 10px; padding-right: 10px; }
        .assistant-input-row { grid-template-columns: minmax(0, 1fr); }
        .assistant-input-actions { width: 100%; min-width: 0; }
        .assistant-send { width: 100%; }
        .topbar-actions .icon-button[data-action="refresh"] { display: none; }
        .trip-select { width: min(36vw, 140px); }
      }

      @media (max-width: 900px) {
        .assistant-layout { grid-template-columns: 1fr; }
        .assistant-status-grid { grid-template-columns: 1fr; }
        .assistant-basket { position: static; }
        .assistant-chat { min-height: 620px; }
        .hero-card.with-image { grid-template-columns: 1fr; }
        .hero-image { max-height: 340px; }
        .stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .readiness-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .overview-technical > summary { align-items: flex-start; flex-direction: column; }
        .route-layout { grid-template-columns: 1fr; }
        .day-facts { position: static; }
        .trip-select { min-width: 0; width: min(42vw, 280px); }
      }
      @media (max-width: 680px) {
        .assistant-toolbar { align-items: stretch; flex-direction: column; }
        .assistant-toolbar-primary .assistant-main-actions { display: grid; grid-template-columns: 1fr; }
        .assistant-toolbar-primary .assistant-main-actions button { width: 100%; }
        .assistant-technical > summary { align-items: flex-start; flex-direction: column; }
        .assistant-technical-actions { align-items: stretch; flex-direction: column; }
        .assistant-technical-actions > * { width: 100%; justify-content: center; }
        .assistant-composer-heading { align-items: flex-start; flex-direction: column; gap: 4px; }
        .assistant-toolbar-actions { justify-content: flex-start; }
        .assistant-retry-notice { align-items: flex-start; flex-direction: column; }
        .assistant-retry-notice button { margin-left: 0; width: 100%; }
        .assistant-chat { min-height: calc(100vh - 210px); }
        .assistant-thread { padding: 14px 10px; overflow-x: hidden; }
        .assistant-message { width: 100%; max-width: 100%; min-width: 0; }
        .assistant-message .message-body { flex: 1 1 0%; max-width: calc(100% - 45px); }
        .quick-prompt-grid { grid-template-columns: 1fr; }
        .quick-prompt-grid button { min-height: 76px; }
        .assistant-input-row { grid-template-columns: 1fr; }
        .assistant-input-actions { width: 100%; }
        .assistant-attach { width: 52px; }
        .assistant-send { width: 100%; }
        .archive-card-grid, .day-archive-grid, .attachment-purpose-grid { grid-template-columns: 1fr; }
        .import-card { grid-template-columns: 1fr; }
        .import-card-icon { width: 46px; height: 46px; }
        .import-card-title { flex-direction: column; }
        .import-toolbar { align-items: stretch; flex-direction: column; }
        .archive-row { grid-template-columns: auto minmax(0, 1fr) auto; }
        .archive-row-value { grid-column: 2; text-align: left; }
        .archive-row-actions { grid-column: 3; grid-row: 1 / span 2; flex-direction: column; }
        .archive-analysis-todo { grid-template-columns: 1fr; }
        .archive-analysis-todo .checkbox-field, .archive-analysis-todo .form-field.full { grid-column: auto; }
        .decision-intro, .media-toolbar { align-items: stretch; flex-direction: column; }
        .decision-slide { grid-template-columns: 1fr; }
        .experience-album.compact .experience-album-strip { grid-template-columns: repeat(4, minmax(0, 1fr)); }
        .decision-image { min-height: 280px; }
        .decision-option-gallery { min-height: 360px; grid-template-rows: minmax(0, 1fr) 78px; }
        .destination-gallery-inline { grid-template-columns: auto minmax(0, 1fr); }
        .destination-gallery-inline .text-button { grid-column: 1 / -1; justify-self: start; }
        .decision-procon { grid-template-columns: 1fr; }
        .decision-footer { grid-template-columns: auto 1fr auto; }
        .decision-footer > .text-button { grid-column: 1 / -1; }
        .media-stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .media-toolbar-actions { justify-content: flex-start; }
        .assistant-setup { grid-template-columns: 1fr; }
        .topbar { padding-left: 8px; padding-right: 8px; min-height: 58px; }
        .menu-button { display: grid; }
        .app-icon { display: none; }
        .title-group h1 { font-size: 17px; }
        .view-badge { display: none; }
        .trip-select { width: min(36vw, 140px); max-width: 100%; padding: 5px 7px; }
        .topbar-actions .icon-button[data-action="refresh"] { display: none; }
        .trip-select ha-icon { display: none; }
        .topbar-actions { gap: 2px; }
        .navigation-shell { grid-template-columns: 1fr; }
        .tabs { padding: 0 4px; }
        .primary-tabs .tab { min-width: 82px; padding: 0 8px; font-size: 12px; flex-direction: column; gap: 3px; }
        .primary-tabs .tab ha-icon { --mdc-icon-size: 20px; }
        .tool-tabs { position: static; margin: 0 8px 6px; justify-self: stretch; }
        .tool-tabs > summary { width: 100%; justify-content: center; min-height: 38px; }
        .tool-tab-grid { position: static; width: 100%; margin-top: 6px; grid-template-columns: 1fr; box-shadow: none; }
        .tab { padding: 0 12px; min-height: 50px; }
        .tab span:not(.count-badge) { font-size: 12px; }
        .content { padding: 14px 10px max(24px, calc(14px + env(safe-area-inset-bottom))); }
        .panel-card, .toolbar-card, .route-flow-card, .handoff-card { padding: 16px; border-radius: 18px; }
        .toolbar-card, .day-toolbar { align-items: stretch; flex-direction: column; }
        .day-toolbar .day-select { min-width: 0; }
        .hero-copy { padding: 22px 18px; }
        .hero-copy h2 { font-size: 31px; }
        .day-cover-hero { grid-template-columns: 1fr; }
        .day-cover-image, .day-cover-image .destination-image { min-height: 210px; }
        .day-cover-copy { padding: 18px 16px; }
        .stat-grid { gap: 9px; }
        .stat-card { min-height: 105px; padding: 14px; border-radius: 16px; }
        .stat-card strong { font-size: 21px; }
        .next-day-grid, .facts-grid, .preview-grid { grid-template-columns: 1fr; }
        .map-stage { height: 46vh; min-height: 300px; }
        .image-gallery, .stop-grid, .trip-grid, .image-search-grid { grid-template-columns: 1fr; }
        .total-day-card { grid-template-columns: 44px 1fr auto; }
        .total-day-image { display: none; }
        .form-grid { grid-template-columns: 1fr; padding: 8px 16px 18px; }
        .form-field.full, .form-section.full, .modal-actions.full { grid-column: auto; }
        .place-enrichment-body { padding-left: 12px; padding-right: 12px; }
        .place-enrichment-item { padding: 12px; }
        .place-enrichment-item > header { display: grid; }
        .place-candidate-details { grid-template-columns: 1fr; }
        .place-image-strip { height: 108px; }
        .place-enrichment-actions { display: grid; grid-template-columns: 1fr; }
        .modal-backdrop { align-items: flex-end; padding: 0; }
        .modal { width: 100%; max-height: 92%; border-radius: 24px 24px 0 0; padding-bottom: env(safe-area-inset-bottom); }
        .modal-header { padding: 18px 16px 10px; }
        .modal-actions { padding-left: 16px; padding-right: 16px; }
        .toast-host { left: 10px; right: 10px; top: max(10px, env(safe-area-inset-top)); }
        .toast { max-width: 100%; }
        .view-notice { align-items: flex-start; flex-wrap: wrap; }
        .compact-button { margin-left: 0; width: 100%; }
      }
      .integrity-card { display: grid; gap: 16px; }
      .integrity-card.status-ready { border-color: color-mix(in srgb, var(--success-color, #2e7d32) 40%, var(--divider-color)); }
      .integrity-card.status-attention { border-color: color-mix(in srgb, var(--warning-color, #ef9a00) 45%, var(--divider-color)); }
      .integrity-card.status-incomplete { border-color: color-mix(in srgb, var(--error-color, #d32f2f) 45%, var(--divider-color)); }
      .integrity-card-main { display: flex; gap: 16px; align-items: center; min-width: 0; }
      .integrity-score { flex: 0 0 78px; width: 78px; height: 78px; border-radius: 50%; display: grid; place-content: center; grid-template-columns: auto auto; align-items: baseline; background: var(--secondary-background-color); border: 5px solid var(--primary-color); }
      .integrity-score strong { font-size: 27px; line-height: 1; }
      .integrity-score span { font-size: 12px; font-weight: 800; }
      .integrity-copy { min-width: 0; }
      .integrity-copy h2 { margin: 3px 0 5px; }
      .integrity-copy p { margin: 0; color: var(--secondary-text-color); overflow-wrap: anywhere; }
      .integrity-dimensions { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
      .integrity-dimensions > div { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 5px 8px; align-items: center; }
      .integrity-dimensions span { color: var(--secondary-text-color); font-size: 12px; }
      .integrity-dimensions strong { font-size: 13px; }
      .integrity-dimensions i { grid-column: 1 / -1; height: 6px; border-radius: 999px; overflow: hidden; background: var(--secondary-background-color); }
      .integrity-dimensions i b { display: block; height: 100%; background: var(--primary-color); border-radius: inherit; }
      .integrity-summary { display: flex; flex-wrap: wrap; gap: 8px; }
      .integrity-summary span { display: inline-flex; align-items: center; gap: 6px; padding: 7px 10px; border-radius: 999px; background: var(--secondary-background-color); font-size: 12px; font-weight: 700; }
      .integrity-summary ha-icon { --mdc-icon-size: 17px; }
      .integrity-dialog-body { padding: 0 20px 10px; display: grid; gap: 16px; max-height: min(68vh, 720px); overflow: auto; }
      .integrity-dialog-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
      .integrity-dialog-stats span { display: grid; gap: 3px; padding: 12px; border-radius: 13px; background: var(--secondary-background-color); color: var(--secondary-text-color); font-size: 12px; }
      .integrity-dialog-stats strong { color: var(--primary-text-color); font-size: 21px; }
      .integrity-issue-list { display: grid; gap: 9px; }
      .integrity-issue { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 10px; align-items: start; padding: 12px; border-radius: 14px; border: 1px solid var(--divider-color); background: var(--card-background-color); }
      .integrity-issue > ha-icon { margin-top: 2px; }
      .integrity-issue > div { min-width: 0; display: grid; gap: 4px; }
      .integrity-issue span, .integrity-issue small { color: var(--secondary-text-color); line-height: 1.4; overflow-wrap: anywhere; }
      .integrity-issue.severity-error > ha-icon { color: var(--error-color, #d32f2f); }
      .integrity-issue.severity-warning > ha-icon { color: var(--warning-color, #ef9a00); }
      .integrity-issue.severity-info > ha-icon { color: var(--info-color, #1976d2); }
      .integrity-dialog-actions { flex-wrap: wrap; }

      @media (max-width: 680px) {
        .integrity-card-main { align-items: flex-start; }
        .integrity-score { flex-basis: 64px; width: 64px; height: 64px; border-width: 4px; }
        .integrity-score strong { font-size: 22px; }
        .integrity-dimensions { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .integrity-dialog-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .integrity-issue { grid-template-columns: auto minmax(0, 1fr); }
        .integrity-issue > .text-button { grid-column: 2; justify-self: start; }
        .integrity-dialog-actions > * { width: 100%; justify-content: center; }
      }

      @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after { scroll-behavior: auto !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; }
      }
    </style>`;
